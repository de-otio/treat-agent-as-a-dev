import Foundation

/// Meta item: `{"v":1,"slug":…,"app_id":N,"app_slug":…,"imported_at":"<ISO8601>"}`.
public struct MetaRecord: Equatable, Sendable {
    public let slug: String
    public let appId: Int
    public let appSlug: String
    public let importedAt: String

    public var json: JSON {
        .object(["v": .int(1), "slug": .string(slug), "app_id": .int(appId),
                 "app_slug": .string(appSlug), "imported_at": .string(importedAt)])
    }

    public static func parse(_ data: Data) -> MetaRecord? {
        guard let j = JSON.parse(data), j["v"]?.intValue == 1,
              let slug = j["slug"]?.stringValue, let id = j["app_id"]?.intValue, id >= 1,
              let appSlug = j["app_slug"]?.stringValue, let at = j["imported_at"]?.stringValue
        else { return nil }
        return MetaRecord(slug: slug, appId: id, appSlug: appSlug, importedAt: at)
    }
}

/// Token-cache item. `account` is stored too so a cache hit can answer without asking GitHub.
public struct CachedToken: Equatable, Sendable {
    public let grant: TokenGrant
    public let account: String

    public static let freshnessMargin = 300

    public var json: JSON {
        .object(["v": .int(1), "token": .string(grant.token), "expires_at": .string(grant.expiresAt),
                 "permissions": .object(grant.permissions.mapValues { .string($0) }),
                 "repository_selection": .string(grant.repositorySelection),
                 "repositories": .array(grant.repositories.map { .string($0) }),
                 "account": .string(account)])
    }

    public static func parse(_ data: Data) -> CachedToken? {
        guard let j = JSON.parse(data), j["v"]?.intValue == 1,
              let account = j["account"]?.stringValue,
              let permsObj = j["permissions"]?.objectValue,
              let reposArr = j["repositories"]?.arrayValue
        else { return nil }
        var perms: [String: String] = [:]
        for (k, v) in permsObj { guard let s = v.stringValue else { return nil }; perms[k] = s }
        var repos: [String] = []
        for r in reposArr { guard let s = r.stringValue else { return nil }; repos.append(s) }
        guard let grant = TokenGrant.parse(JSON.object([
            "token": j["token"] ?? .null, "expires_at": j["expires_at"] ?? .null,
            "permissions": .object(permsObj),
            "repository_selection": j["repository_selection"] ?? .null,
            "repositories": .array(repos.map { .object(["name": .string($0)]) }),
        ])) else { return nil }
        return CachedToken(grant: grant, account: account)
    }

    /// Seconds until expiry (negative when expired).
    public func secondsRemaining(now: Date) -> Double {
        (Time.parseISO8601(grant.expiresAt) ?? .distantPast).timeIntervalSince(now)
    }

    /// Strictly more than 300 s left.
    public func isFresh(now: Date) -> Bool { secondsRemaining(now: now) > Double(CachedToken.freshnessMargin) }
}

/// What `mint` prints.
public func mintOutput(grant: TokenGrant, account: String, cached: Bool) -> JSON {
    .object(["token": .string(grant.token), "expires_at": .string(grant.expiresAt),
             "permissions": .object(grant.permissions.mapValues { .string($0) }),
             "repository_selection": .string(grant.repositorySelection),
             "repositories": .array(grant.repositories.map { .string($0) }),
             "cached": .bool(cached), "account": .string(account)])
}

public enum RateLimit {
    public static let window = 600
    public static let maxPrompts = 3

    public enum Decision: Equatable, Sendable {
        /// Proceed; persist `updatedLog` (pruned, plus now) before prompting.
        case allow(updatedLog: [Int])
        /// Refuse. `replacementLog` is non-nil only for a corrupt log, which is overwritten as saturated.
        case limited(retryAfter: Int, replacementLog: [Int]?)
    }

    public static func encode(_ prompts: [Int]) -> Data {
        JSON.object(["v": .int(1), "prompts": .array(prompts.map { .int($0) })]).serializedData()
    }

    static func parse(_ data: Data) -> [Int]? {
        guard let j = JSON.parse(data), j["v"]?.intValue == 1, let arr = j["prompts"]?.arrayValue else { return nil }
        var out: [Int] = []
        for e in arr { guard let i = e.intValue else { return nil }; out.append(i) }
        return out
    }

    /// Entries 600 s old or more are dropped. With 3 or more left the request is refused.
    public static func decide(stored: Data?, now: Int) -> Decision {
        var prompts: [Int] = []
        if let stored {
            guard let p = parse(stored) else {
                return .limited(retryAfter: window, replacementLog: [now, now, now])
            }
            prompts = p
        }
        let live = prompts.filter { now - $0 < window }
        if live.count >= maxPrompts {
            let oldest = live.min() ?? now
            return .limited(retryAfter: max(1, min(window, oldest + window - now)), replacementLog: nil)
        }
        return .allow(updatedLog: live + [now])
    }
}
