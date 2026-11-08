import Foundation

/// Command orchestration against the protocols. All decisions live here and in the pure types above.
public struct Signer {
    let env: Environment
    public init(environment: Environment) { self.env = environment }

    // MARK: shared helpers

    func loadMeta(_ slug: String) throws -> MetaRecord? {
        guard let data = try env.store.get(.meta, account: slug) else { return nil }
        guard let m = MetaRecord.parse(data), m.slug == slug else {
            throw SignerError.keychain(osstatus: -26275, message: "meta item for \(slug) is unreadable")
        }
        return m
    }

    func requireKey(_ slug: String) throws -> (meta: MetaRecord, key: any SigningKey) {
        guard let meta = try loadMeta(slug), let key = try env.keys.find(slug: slug) else {
            throw SignerError.notFound(slug: slug)
        }
        return (meta, key)
    }

    static func errorMessage(from body: Data) -> String? {
        guard let m = JSON.parse(body)?["message"]?.stringValue else { return nil }
        // Defence in depth: whatever GitHub says, never relay anything shaped like a token or a JWT.
        var scrubbed = m
        for pattern in ["(gh[pousr]_|github_pat_)[A-Za-z0-9_]+", "eyJ[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]*"] {
            scrubbed = scrubbed.replacingOccurrences(of: pattern, with: "[redacted]", options: .regularExpression)
        }
        let clean = String(String.UnicodeScalarView(scrubbed.unicodeScalars.filter { $0.value >= 0x20 && $0.value != 0x7F }.prefix(200)))
        return clean.isEmpty ? nil : clean
    }

    /// 2xx with a JSON object or array body, or a `github` error.
    static func checked(_ r: HTTPResponse) throws -> JSON {
        guard (200...299).contains(r.status) else {
            let detail = errorMessage(from: r.body).map { ": \($0)" } ?? ""
            throw SignerError.github(status: r.status, message: "GitHub returned HTTP \(r.status)\(detail)")
        }
        guard let j = JSON.parse(r.body) else {
            throw SignerError.github(status: r.status, message: "GitHub returned a malformed response")
        }
        return j
    }

    func get(_ path: String, appId: Int, key: any SigningKey) throws -> JSON {
        let jwt = try JWT.make(appId: appId, now: env.clock.unixSeconds(), key: key)
        return try Signer.checked(try env.github.get(path: path, jwt: jwt))
    }

    func requirePresence(_ reason: String) throws {
        guard env.presence.approve(reason: reason) else {
            throw SignerError.presenceDenied("presence was denied or is unavailable")
        }
    }

    /// The one prompt budget: rate check, log write and prompt run under the exclusive lock.
    func promptWithinBudget(slug: String, reason: String) throws {
        try env.lock.withExclusive {
            let now = env.clock.unixSeconds()
            switch RateLimit.decide(stored: try env.store.get(.rate, account: slug), now: now) {
            case .allow(let log):
                try env.store.put(.rate, account: slug, data: RateLimit.encode(log))
            case .limited(let retryAfter, let replacement):
                if let replacement { try env.store.put(.rate, account: slug, data: RateLimit.encode(replacement)) }
                throw SignerError.rateLimited(retryAfter: retryAfter)
            }
            try requirePresence(reason)
        }
    }

    /// Clears the key, its meta and token cache. The prompt log is kept: it is the budget for every prompt,
    /// so delete/replace must not reset it.
    func clearSlug(_ slug: String) throws {
        try env.keys.delete(slug: slug)
        for account in try env.store.list(.token, accountPrefix: slug + "/") {
            try env.store.delete(.token, account: account)
        }
        try env.store.delete(.meta, account: slug)
    }

    // MARK: commands

    public func run(_ command: Command, readStdin: () throws -> Data) throws -> JSON {
        switch command {
        case .version:
            return .object(["version": .string(SignerInfo.version), "identifier": .string(SignerInfo.identifier)])
        case .status(let slug): return try status(slug)
        case .importKey(let slug, let appId, let replace): return try importKey(slug, appId, replace, pem: try readStdin())
        case .mint(let slug, let installId, let n): return try mint(slug, installId, n)
        case .appMeta(let slug):
            let (meta, key) = try requireKey(slug)
            let j = try get("/app", appId: meta.appId, key: key)
            guard j.objectValue != nil else { throw SignerError.github(status: 200, message: "GitHub returned an unexpected response shape") }
            return j
        case .installations(let slug):
            let (meta, key) = try requireKey(slug)
            let j = try get("/app/installations?per_page=100", appId: meta.appId, key: key)
            guard j.arrayValue != nil else { throw SignerError.github(status: 200, message: "GitHub returned an unexpected response shape") }
            return j
        case .installation(let slug, let installId):
            let (meta, key) = try requireKey(slug)
            let j = try get("/app/installations/\(installId)", appId: meta.appId, key: key)
            guard j.objectValue != nil else { throw SignerError.github(status: 200, message: "GitHub returned an unexpected response shape") }
            return j
        case .delete(let slug): return try delete(slug)
        }
    }

    func status(_ slug: String) throws -> JSON {
        let key = try env.keys.find(slug: slug)
        let meta = try loadMeta(slug)
        return .object(["slug": .string(slug), "present": .bool(key != nil),
                        "app_id": meta.map { .int($0.appId) } ?? .null])
    }

    func delete(_ slug: String) throws -> JSON {
        let key = try env.keys.find(slug: slug)
        let meta = try? loadMeta(slug)
        guard key != nil || meta != nil else { throw SignerError.notFound(slug: slug) }
        try promptWithinBudget(slug: slug, reason: Reasons.deleteKey(slug: slug))
        try clearSlug(slug)
        return .object(["slug": .string(slug), "deleted": .bool(true)])
    }

    func importKey(_ slug: String, _ appId: Int, _ replace: Bool, pem: Data) throws -> JSON {
        guard pem.count <= PEM.maxInputBytes else { throw SignerError.badKey("input larger than 64 KiB") }
        let existed = try env.keys.find(slug: slug) != nil
        if existed && !replace { throw SignerError.exists(slug: slug) }
        // A key without its meta item (left by a failed write) has no stored App ID to pin the replacement to.
        if replace && existed, try loadMeta(slug) == nil {
            throw SignerError.keychain(osstatus: -25300, message: "the key for \(slug) has no meta item, so its App ID cannot be checked; delete it first")
        }
        if replace, let stored = try loadMeta(slug), stored.appId != appId {
            throw SignerError.appIdChanged(stored: stored.appId, requested: appId)
        }
        let key = try env.keys.makeKey(pkcs1DER: try PEM.parsePrivateKey(pem))
        try promptWithinBudget(slug: slug, reason: replace ? Reasons.replaceKey(slug: slug, appId: appId) : Reasons.importKey(slug: slug, appId: appId))

        // Verify before touching the store.
        let app = try get("/app", appId: appId, key: key)
        guard let id = app["id"]?.intValue, let appSlug = app["slug"]?.stringValue else {
            throw SignerError.github(status: 200, message: "GET /app returned an unexpected response")
        }
        guard id == appId else { throw SignerError.appMismatch(expected: appId, actual: id) }

        if replace { try clearSlug(slug) }
        try env.keys.add(key, slug: slug)
        let meta = MetaRecord(slug: slug, appId: appId, appSlug: appSlug, importedAt: Time.formatISO8601(env.clock.now()))
        do {
            try env.store.put(.meta, account: slug, data: meta.json.serializedData())
        } catch {
            try? env.keys.delete(slug: slug) // do not leave a key without meta
            throw error
        }
        return .object(["slug": .string(slug), "app_id": .int(appId), "app_slug": .string(appSlug), "replaced": .bool(existed)])
    }

    func mint(_ slug: String, _ installId: Int, _ narrowing: Narrowing) throws -> JSON {
        // 1. meta and key
        let (meta, key) = try requireKey(slug)
        // 2. narrowing hash
        let cacheAccount = "\(slug)/\(installId)/\(narrowing.hash)"
        // 3. cache
        let start = env.clock.now()
        if let data = try env.store.get(.token, account: cacheAccount) {
            if let cached = CachedToken.parse(data), !Widening.violates(requested: narrowing, granted: cached.grant) {
                if cached.isFresh(now: start) { return mintOutput(grant: cached.grant, account: cached.account, cached: true) }
            } else {
                try env.store.delete(.token, account: cacheAccount)
            }
        }
        // 4. installation lookup (no prompt if this fails)
        let inst = try get("/app/installations/\(installId)", appId: meta.appId, key: key)
        guard let login = inst["account"]?["login"]?.stringValue, Validate.isPlausibleLogin(login) else {
            throw SignerError.github(status: 200, message: "installation response has no usable account login")
        }
        // 5+6. one prompt budget (rate check, log write, prompt), serialised
        try promptWithinBudget(slug: slug, reason: Reasons.mint(slug: slug, account: login, narrowing: narrowing))
        // 7. POST (fresh JWT: the prompt may have taken a while)
        let jwt = try JWT.make(appId: meta.appId, now: env.clock.unixSeconds(), key: key)
        let response = try env.github.post(path: "/app/installations/\(installId)/access_tokens", jwt: jwt, body: narrowing.requestBody)
        let json = try Signer.checked(response)
        guard let grant = TokenGrant.parse(json) else {
            throw SignerError.github(status: response.status, message: "GitHub returned a token response without a usable token or expiry")
        }
        // 8. widening check
        if Widening.violates(requested: narrowing, granted: grant) { throw SignerError.widened }
        // 9. cache, then print
        try env.store.put(.token, account: cacheAccount,
                          data: CachedToken(grant: grant, account: login).json.serializedData())
        return mintOutput(grant: grant, account: login, cached: false)
    }
}
