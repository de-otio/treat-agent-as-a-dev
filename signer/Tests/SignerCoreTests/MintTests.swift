import XCTest
@testable import SignerCore

final class MintTests: XCTestCase {
    let mintArgs = ["mint", "app", "42", "--repo", "Repo-One", "--perm", "contents=read"]

    func cacheKey(_ args: [String]) throws -> StoreKey {
        guard case .mint(let slug, let id, let n) = try Arguments.parse(args) else { fatalError() }
        return StoreKey(kind: .token, account: "\(slug)/\(id)/\(n.hash)")
    }

    // MARK: happy path

    func testMintPrintsTokenAndCaches() throws {
        let w = World()
        let r = w.run(mintArgs)
        XCTAssertEqual(r.exitCode, 0)
        XCTAssertEqual(r.stderr, "")
        let j = stdoutJSON(r)
        XCTAssertEqual(j["token"]?.stringValue, secretToken)
        XCTAssertEqual(j["cached"], .bool(false))
        XCTAssertEqual(j["account"]?.stringValue, "octo-org")
        XCTAssertEqual(j["repositories"], .array([.string("Repo-One")]))
        XCTAssertEqual(w.presence.reasons, ["mint a GitHub token for app on octo-org (permissions: contents:read; repositories: repo-one)"])
        XCTAssertNotNil(w.store.items[try cacheKey(mintArgs)])
        let post = try XCTUnwrap(w.github.posts.first)
        XCTAssertEqual(post.path, "/app/installations/42/access_tokens")
        XCTAssertEqual(JSON.parse(post.body), .object(["repositories": .array([.string("Repo-One")]),
                                                        "permissions": .object(["contents": .string("read")])]))
    }

    func testBodyIsEmptyWithNoNarrowingAndOnlyRequestedPartsOtherwise() throws {
        let w = World()
        w.run(["mint", "app", "42"])
        XCTAssertEqual(w.github.posts[0].body, Data())
        let w2 = World()
        w2.run(["mint", "app", "42", "--perm", "issues=write"])
        XCTAssertEqual(JSON.parse(w2.github.posts[0].body), .object(["permissions": .object(["issues": .string("write")])]))
        let w3 = World()
        w3.run(["mint", "app", "42", "--repo", "x"])
        XCTAssertEqual(JSON.parse(w3.github.posts[0].body), .object(["repositories": .array([.string("x")])]))
    }

    func testUnknownSlugAndMissingKeyAreNotFound() {
        assertFails(World(withKey: false).run(mintArgs), "not-found", exit: 3)
        let w = World()
        w.keys.keys["app"] = nil // meta without key
        assertFails(w.run(mintArgs), "not-found", exit: 3)
        XCTAssertTrue(w.github.calls.isEmpty)
    }

    func testAmbiguousKeyIsKeychainError() {
        let w = World()
        w.keys.ambiguous = ["app"]
        let r = w.run(mintArgs)
        assertFails(r, "keychain", exit: 8)
        XCTAssertNotNil(stderrJSON(r)["osstatus"])
        XCTAssertTrue(w.github.calls.isEmpty)
    }

    // MARK: cache

    func testCacheHitNoPromptNoRateWriteNoHTTP() throws {
        let w = World()
        XCTAssertEqual(w.run(mintArgs).exitCode, 0)
        let rateBefore = w.store.items[StoreKey(kind: .rate, account: "app")]
        let callsBefore = w.github.calls.count
        w.trace.events = []
        w.clock.advance(60)
        let r = w.run(mintArgs)
        XCTAssertEqual(r.exitCode, 0)
        XCTAssertEqual(stdoutJSON(r)["cached"], .bool(true))
        XCTAssertEqual(stdoutJSON(r)["token"]?.stringValue, secretToken)
        XCTAssertEqual(stdoutJSON(r)["account"]?.stringValue, "octo-org")
        XCTAssertEqual(w.presence.reasons.count, 1, "no second prompt")
        XCTAssertEqual(w.github.calls.count, callsBefore, "no HTTP at all on a hit (no POST, no GET)")
        XCTAssertEqual(w.store.items[StoreKey(kind: .rate, account: "app")], rateBefore, "rate log untouched")
        XCTAssertEqual(w.trace.events, [], "no store writes")
    }

    func testCacheFreshnessBoundaryIs300Seconds() throws {
        for (remaining, expectHit) in [(301, true), (300, false), (299, false), (-5, false)] {
            let w = World()
            let key = try cacheKey(mintArgs)
            let exp = Time.formatISO8601(w.clock.now().addingTimeInterval(TimeInterval(remaining)))
            let grant = TokenGrant(token: "cached-token", expiresAt: exp, permissions: ["contents": "read"],
                                   repositorySelection: "selected", repositories: ["Repo-One"])
            w.store.items[key] = CachedToken(grant: grant, account: "octo-org").json.serializedData()
            let r = w.run(mintArgs)
            XCTAssertEqual(r.exitCode, 0, "remaining \(remaining)")
            XCTAssertEqual(stdoutJSON(r)["cached"], .bool(expectHit), "remaining \(remaining)")
            XCTAssertEqual(w.presence.reasons.count, expectHit ? 0 : 1, "remaining \(remaining)")
            XCTAssertEqual(stdoutJSON(r)["token"]?.stringValue, expectHit ? "cached-token" : secretToken)
        }
    }

    func testDifferentNarrowingNeverHitsTheCache() throws {
        let w = World()
        w.run(mintArgs)
        w.run(["mint", "app", "42", "--repo", "Repo-One", "--perm", "contents=write"])
        w.run(["mint", "app", "43", "--repo", "Repo-One", "--perm", "contents=read"])
        XCTAssertEqual(w.presence.reasons.count, 3)
        XCTAssertEqual(w.store.accounts(.token).count, 3)
    }

    func testEquivalentNarrowingSpellingsShareACacheItem() {
        let w = World()
        w.run(["mint", "app", "42", "--repo", "B", "--repo", "a", "--perm", "x=read", "--perm", "a_b=write"])
        let r = w.run(["mint", "app", "42", "--perm", "a_b=write", "--repo", "A", "--repo", "b", "--repo", "B", "--perm", "x=read"])
        XCTAssertEqual(stdoutJSON(r)["cached"], .bool(true))
    }

    func testUnparseableCacheItemIsDeletedAndTreatedAsMiss() throws {
        let w = World()
        let key = try cacheKey(mintArgs)
        w.store.items[key] = Data("not json".utf8)
        w.trace.events = []
        let r = w.run(mintArgs)
        XCTAssertEqual(stdoutJSON(r)["cached"], .bool(false))
        XCTAssertTrue(w.trace.events.contains("store.delete.taaad-signer.token"))
        XCTAssertEqual(w.presence.reasons.count, 1)
        XCTAssertEqual(CachedToken.parse(w.store.items[key]!)?.grant.token, secretToken)
    }

    func testCacheItemBroaderThanRequestedIsDiscarded() throws {
        let w = World()
        let key = try cacheKey(mintArgs)
        let exp = Time.formatISO8601(w.clock.now().addingTimeInterval(3000))
        let wide = TokenGrant(token: "wide", expiresAt: exp, permissions: ["contents": "write"],
                              repositorySelection: "selected", repositories: ["Repo-One"])
        w.store.items[key] = CachedToken(grant: wide, account: "octo-org").json.serializedData()
        let r = w.run(mintArgs)
        XCTAssertEqual(stdoutJSON(r)["cached"], .bool(false))
        XCTAssertEqual(stdoutJSON(r)["token"]?.stringValue, secretToken)
    }

    // MARK: rate limit

    /// Fresh World per call would reset state, so vary the narrowing and make tokens stale at once.
    func testFourthPromptWithin600SecondsIsRefusedWithoutPrompting() {
        let w = World()
        w.tokenLifetime = 100 // never cache-fresh
        for i in 1...3 {
            XCTAssertEqual(w.run(["mint", "app", "42", "--repo", "r\(i)"]).exitCode, 0, "prompt \(i)")
            w.clock.advance(10)
        }
        XCTAssertEqual(w.presence.reasons.count, 3)
        let posts = w.github.posts.count
        let t0 = w.now - 30
        let r = w.run(["mint", "app", "42", "--repo", "r4"])
        assertFails(r, "rate-limited", exit: 5)
        XCTAssertEqual(stderrJSON(r)["retry_after"]?.intValue, t0 + 600 - w.now)
        XCTAssertEqual(w.presence.reasons.count, 3, "no prompt")
        XCTAssertEqual(w.github.posts.count, posts, "no POST")
        XCTAssertEqual(w.rateLog?.count, 3, "a refusal does not extend the log")
    }

    func testDeniedPromptStillCountsAgainstTheBudget() {
        let w = World()
        w.presence.answers = [false, false, false]
        for i in 1...3 {
            assertFails(w.run(["mint", "app", "42", "--repo", "r\(i)"]), "presence-denied", exit: 4)
        }
        XCTAssertEqual(w.rateLog?.count, 3)
        w.presence.defaultAnswer = true
        let r = w.run(["mint", "app", "42", "--repo", "r4"])
        assertFails(r, "rate-limited", exit: 5)
        XCTAssertEqual(w.presence.reasons.count, 3)
    }

    func testRateLogIsWrittenBeforeThePromptIsShown() {
        let w = World()
        var logAtPrompt: [Int]?
        w.presence.onApprove = { logAtPrompt = w.rateLog }
        w.run(mintArgs)
        XCTAssertEqual(logAtPrompt, [w.now])
    }

    func testCorruptRateLogIsSaturated() {
        let w = World()
        w.store.items[StoreKey(kind: .rate, account: "app")] = Data("garbage".utf8)
        let r = w.run(mintArgs)
        assertFails(r, "rate-limited", exit: 5)
        XCTAssertEqual(w.presence.reasons.count, 0)
        XCTAssertEqual(w.rateLog, [w.now, w.now, w.now])
        XCTAssertEqual(w.github.posts.count, 0)
        // wrong shape counts as corrupt too
        let w2 = World()
        w2.store.items[StoreKey(kind: .rate, account: "app")] = Data("{\"v\":1,\"prompts\":[\"x\"]}".utf8)
        assertFails(w2.run(mintArgs), "rate-limited", exit: 5)
    }

    func testRateWindowBoundary() {
        let now = 1_000_000
        // an entry exactly 600 s old is dropped, 599 s old is live
        XCTAssertEqual(RateLimit.decide(stored: RateLimit.encode([now - 600, now - 600, now - 600]), now: now),
                       .allow(updatedLog: [now]))
        XCTAssertEqual(RateLimit.decide(stored: RateLimit.encode([now - 599, now - 599, now - 599]), now: now),
                       .limited(retryAfter: 1, replacementLog: nil))
        XCTAssertEqual(RateLimit.decide(stored: RateLimit.encode([now - 599, now - 10, now - 5]), now: now),
                       .limited(retryAfter: 1, replacementLog: nil))
        XCTAssertEqual(RateLimit.decide(stored: RateLimit.encode([now - 100, now - 50, now - 10]), now: now),
                       .limited(retryAfter: 500, replacementLog: nil))
        XCTAssertEqual(RateLimit.decide(stored: RateLimit.encode([now - 100, now - 50]), now: now),
                       .allow(updatedLog: [now - 100, now - 50, now]))
        XCTAssertEqual(RateLimit.decide(stored: nil, now: now), .allow(updatedLog: [now]))
        // a log with future timestamps cannot park the caller for longer than the window
        XCTAssertEqual(RateLimit.decide(stored: RateLimit.encode([now + 5000, now + 5000, now + 5000]), now: now),
                       .limited(retryAfter: 600, replacementLog: nil))
    }

    // MARK: ordering

    func testDeniedPresenceMeansNoPost() {
        let w = World()
        w.presence.defaultAnswer = false
        let r = w.run(mintArgs)
        assertFails(r, "presence-denied", exit: 4)
        XCTAssertEqual(w.github.posts.count, 0)
        XCTAssertTrue(w.store.accounts(.token).isEmpty)
        XCTAssertEqual(w.presence.reasons.count, 1)
    }

    func testInstallationLookupFailureMeansNoPromptAndNoRateEntry() {
        let w = World()
        w.github.handler = { _ in HTTPResponse(status: 404, body: Data("{\"message\":\"Not Found\"}".utf8)) }
        let r = w.run(mintArgs)
        assertFails(r, "github", exit: 6)
        XCTAssertEqual(stderrJSON(r)["status"]?.intValue, 404)
        XCTAssertTrue(stderrJSON(r)["message"]?.stringValue?.contains("Not Found") == true)
        XCTAssertEqual(w.presence.reasons.count, 0)
        XCTAssertNil(w.rateLog)
        XCTAssertEqual(w.github.calls.count, 1)
    }

    func testTransportErrorOnLookupIsStatusZero() {
        let w = World()
        w.github.handler = { _ in throw SignerError.github(status: 0, message: "transport error") }
        let r = w.run(mintArgs)
        assertFails(r, "github", exit: 6)
        XCTAssertEqual(stderrJSON(r)["status"]?.intValue, 0)
        XCTAssertEqual(w.presence.reasons.count, 0)
    }

    func testHostileAccountLoginNeverReachesThePrompt() {
        let w = World()
        w.github.handler = { c in
            c.method == "GET" ? json(.object(["account": .object(["login": .string("evil\" wants to wipe your disk")])])) : HTTPResponse(status: 500, body: Data())
        }
        assertFails(w.run(mintArgs), "github", exit: 6)
        XCTAssertEqual(w.presence.reasons.count, 0)
    }

    func testOrderOfOperations() {
        let w = World()
        w.run(mintArgs)
        XCTAssertEqual(w.trace.events, [
            "github.GET /app/installations/42",
            "store.put.taaad-signer.rate",
            "github.POST /app/installations/42/access_tokens",
            "store.put.taaad-signer.token",
        ])
    }

    func testJWTsAreFreshPerRequestAndNameTheApp() throws {
        let w = World()
        w.presence.onApprove = { w.clock.advance(120) }
        w.run(mintArgs)
        let iat = { (c: Call) -> Int in
            let payload = c.jwt.split(separator: ".")[1]
            var s = payload.replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
            while s.count % 4 != 0 { s += "=" }
            return JSON.parse(Data(base64Encoded: s)!)!["iat"]!.intValue!
        }
        XCTAssertEqual(iat(w.github.calls[1]) - iat(w.github.calls[0]), 120)
    }

    // MARK: widening

    func testWideningRandomised() throws {
        var rng = SplitMix64(seed: 0xC0FFEE)
        let permNames = ["contents", "issues", "pull_requests", "metadata", "actions"]
        let levels = ["read", "write", "admin"]
        let repoPool = ["alpha", "Beta", "gamma", "delta", "eps"]
        for i in 0..<300 {
            let w = World()
            let reqPerms = permNames.shuffled(using: &rng).prefix(Int.random(in: 0...3, using: &rng))
                .map { ($0, levels.randomElement(using: &rng)!) }
            let reqRepos = Array(repoPool.shuffled(using: &rng).prefix(Int.random(in: 0...3, using: &rng)))
            var args = ["mint", "app", "42"]
            for p in reqPerms { args += ["--perm", "\(p.0)=\(p.1)"] }
            for r in reqRepos { args += ["--repo", r] }

            var respPerms: [String: JSON] = [:]
            for p in reqPerms where Bool.random(using: &rng) {
                let rank = levels.firstIndex(of: p.1)!
                respPerms[p.0] = .string(levels[Int.random(in: 0...rank, using: &rng)])
            }
            if !reqPerms.isEmpty, respPerms["metadata"] == nil, Bool.random(using: &rng) { respPerms["metadata"] = .string("read") }
            var respRepos = reqRepos.filter { _ in Bool.random(using: &rng) }.map { Bool.random(using: &rng) ? $0.uppercased() : $0 }
            var selection = reqRepos.isEmpty ? "all" : "selected"

            var kinds: [Int] = []
            if !reqPerms.isEmpty { kinds.append(0) }
            if reqPerms.contains(where: { $0.1 != "admin" }) { kinds.append(1) }
            if !reqRepos.isEmpty { kinds += [2, 3] }
            let violate = !kinds.isEmpty && Bool.random(using: &rng)
            if violate {
                switch kinds.randomElement(using: &rng)! {
                case 0:
                    let free = (permNames + ["workflows"]).first { n in !reqPerms.contains { $0.0 == n } }!
                    respPerms[free] = .string("write") // "read" would be fine for metadata (implicit)
                case 1:
                    let p = reqPerms.first { $0.1 != "admin" }!
                    respPerms[p.0] = .string(levels[levels.firstIndex(of: p.1)! + 1])
                case 2: respRepos.append("not-requested-\(i)")
                default: selection = "all"
                }
            }
            w.github.handler = { c in
                guard c.method == "POST" else { return w.defaultHandler(c) }
                return json(.object(["token": .string(secretToken),
                                     "expires_at": .string(Time.formatISO8601(w.clock.now().addingTimeInterval(3600))),
                                     "repository_selection": .string(selection),
                                     "permissions": .object(respPerms),
                                     "repositories": .array(respRepos.map { .object(["name": .string($0)]) })]), status: 201)
            }
            let r = w.run(args)
            if violate {
                assertFails(r, "widened", exit: 7)
                XCTAssertTrue(w.store.accounts(.token).isEmpty, "iteration \(i): widened token must not be cached")
                XCTAssertFalse(r.stderr.contains(secretToken))
            } else {
                XCTAssertEqual(r.exitCode, 0, "iteration \(i): \(args) \(r.stderr)")
                XCTAssertEqual(w.store.accounts(.token).count, 1)
            }
        }
    }

    func testWideningNamedCases() {
        func widened(_ req: Narrowing, perms: [String: String] = [:], selection: String = "selected", repos: [String] = []) -> Bool {
            Widening.violates(requested: req, granted: TokenGrant(token: "t", expiresAt: "x", permissions: perms,
                                                                   repositorySelection: selection, repositories: repos))
        }
        let n = try! Narrowing(repositories: ["A"], permissions: [("contents", "write")])
        XCTAssertFalse(widened(n, perms: ["contents": "write"], repos: ["a"]))
        XCTAssertFalse(widened(n, perms: ["contents": "read"], repos: ["A"]))
        XCTAssertFalse(widened(n, perms: [:], repos: []))
        XCTAssertTrue(widened(n, perms: ["contents": "admin"], repos: ["a"]))
        XCTAssertTrue(widened(n, perms: ["issues": "read"], repos: ["a"]))
        XCTAssertTrue(widened(n, perms: ["contents": "banana"], repos: ["a"]))
        XCTAssertTrue(widened(n, perms: ["contents": ""], repos: ["a"]))
        XCTAssertTrue(widened(n, perms: ["contents": "read"], repos: ["a", "b"]))
        XCTAssertTrue(widened(n, perms: ["contents": "read"], selection: "all", repos: ["a"]))
        XCTAssertTrue(widened(n, perms: ["contents": "read"], selection: "", repos: ["a"]))
        // nothing requested: nothing to widen
        XCTAssertFalse(widened(.none, perms: ["anything": "admin"], selection: "all", repos: ["x"]))
        // only permissions requested: repositories are unconstrained
        let p = try! Narrowing(repositories: [], permissions: [("contents", "read")])
        XCTAssertFalse(widened(p, perms: ["contents": "read"], selection: "all", repos: ["x"]))
        XCTAssertTrue(widened(p, perms: ["contents": "read", "issues": "read"], selection: "all"))
    }

    func testMalformedTokenResponseIsGithubErrorAndNotCached() {
        let w = World()
        w.github.handler = { c in c.method == "POST" ? json(.object(["token": .string(secretToken)]), status: 201) : w.defaultHandler(c) }
        let r = w.run(mintArgs)
        assertFails(r, "github", exit: 6)
        XCTAssertFalse(r.stderr.contains(secretToken))
        XCTAssertTrue(w.store.accounts(.token).isEmpty)
    }

    func testPostFailureIsReportedWithStatus() {
        let w = World()
        w.github.handler = { c in c.method == "POST" ? HTTPResponse(status: 422, body: Data("{\"message\":\"Validation Failed\"}".utf8)) : w.defaultHandler(c) }
        let r = w.run(mintArgs)
        assertFails(r, "github", exit: 6)
        XCTAssertEqual(stderrJSON(r)["status"]?.intValue, 422)
    }
}
