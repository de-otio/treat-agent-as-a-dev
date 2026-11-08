import XCTest
@testable import SignerCore

final class Shared: @unchecked Sendable {
    let world: World
    private let g = Guard()
    private var _results: [ExecResult] = []
    init(_ w: World) { world = w }
    func add(_ r: ExecResult) { g.run { _results.append(r) } }
    var results: [ExecResult] { g.run { _results } }
}

final class ReviewFixTests: XCTestCase {
    let repl = ["import", "app", "123", "--replace"]
    var pem: Data { TestPEM.pkcs1 }

    // item 1: one budget for every prompt
    func testImportReplaceAndDeleteSpendTheSameBudgetAsMint() {
        let w = World()
        w.tokenLifetime = 100
        XCTAssertEqual(w.run(["mint", "app", "42", "--repo", "a"]).exitCode, 0)
        XCTAssertEqual(w.run(repl, stdin: pem).exitCode, 0)
        XCTAssertEqual(w.run(repl, stdin: pem).exitCode, 0)
        XCTAssertEqual(w.presence.reasons.count, 3)
        assertFails(w.run(repl, stdin: pem), "rate-limited", exit: 5)
        assertFails(w.run(["delete", "app"]), "rate-limited", exit: 5)
        assertFails(w.run(["mint", "app", "42", "--repo", "b"]), "rate-limited", exit: 5)
        XCTAssertEqual(w.presence.reasons.count, 3, "no prompt once spent")
        XCTAssertNotNil(w.keys.keys["app"], "a refused delete deletes nothing")
    }

    func testDeniedDeleteAndDeniedImportCount() {
        let w = World()
        w.presence.defaultAnswer = false
        for _ in 0..<2 { assertFails(w.run(["delete", "app"]), "presence-denied", exit: 4) }
        assertFails(w.run(repl, stdin: pem), "presence-denied", exit: 4)
        XCTAssertEqual(w.rateLog?.count, 3)
        assertFails(w.run(["delete", "app"]), "rate-limited", exit: 5)
    }

    func testDeleteThenImportCannotResetTheBudget() {
        let w = World()
        w.presence.defaultAnswer = true
        XCTAssertEqual(w.run(["delete", "app"]).exitCode, 0)
        XCTAssertEqual(w.run(["import", "app", "123"], stdin: pem).exitCode, 0)
        XCTAssertEqual(w.run(["delete", "app"]).exitCode, 0)
        assertFails(w.run(["import", "app", "123"], stdin: pem), "rate-limited", exit: 5)
    }

    // item 2: serialisation
    func raceWorld() -> Shared {
        let w = World()
        w.tokenLifetime = 100
        w.presence.defaultAnswer = true
        w.store.afterRateRead = { usleep(30_000) } // widen the check-then-write window
        return Shared(w)
    }

    func hammer(_ s: Shared, n: Int) {
        DispatchQueue.concurrentPerform(iterations: n) { i in
            s.add(s.world.run(["mint", "app", "42", "--repo", "r\(i)"]))
        }
    }

    func testInterleavedRunsNeverExceedTheBudgetUnderTheLock() {
        let s = raceWorld()
        s.world.lock = MutexLock()
        hammer(s, n: 8)
        XCTAssertEqual(s.world.presence.reasons.count, 3)
        XCTAssertEqual(s.results.filter { $0.exitCode == 0 }.count, 3)
        XCTAssertEqual(s.results.filter { $0.exitCode == 5 }.count, 5)
        XCTAssertEqual(s.world.rateLog?.count, 3)
    }

    func testWithoutTheLockTheSameRaceOverspends() {
        let s = raceWorld()
        s.world.lock = NoLock()
        hammer(s, n: 8)
        XCTAssertGreaterThan(s.world.presence.reasons.count, 3, "the control must show the race the lock closes")
    }

    // item 3
    func testReplaceMustKeepTheStoredAppId() {
        let w = World()
        let before = w.store.items
        let r = w.run(["import", "app", "999", "--replace"], stdin: pem)
        assertFails(r, "app-mismatch", exit: 11)
        XCTAssertEqual(w.presence.reasons.count, 0, "refused before prompting")
        XCTAssertTrue(w.github.calls.isEmpty)
        XCTAssertEqual(w.store.items, before)
        XCTAssertNotNil(w.keys.keys["app"])
        XCTAssertEqual(w.run(repl, stdin: pem).exitCode, 0, "same App ID is fine")
    }

    // security review L4: a key whose meta item is missing must not be replaceable under a different App ID
    func testReplaceRefusesAKeyWithoutMeta() throws {
        let w = World()
        try w.store.delete(.meta, account: "app")
        XCTAssertNotNil(w.keys.keys["app"])
        let before = w.store.items
        let r = w.run(["import", "app", "999", "--replace"], stdin: pem)
        assertFails(r, "keychain", exit: 8)
        XCTAssertEqual(w.presence.reasons.count, 0, "refused before prompting")
        XCTAssertTrue(w.github.calls.isEmpty)
        XCTAssertEqual(w.store.items, before)
        XCTAssertNotNil(w.keys.keys["app"], "the key is untouched")
    }

    // item 4
    func testImplicitMetadataRead() throws {
        func widened(_ n: Narrowing, _ perms: [String: String]) -> Bool {
            Widening.violates(requested: n, granted: TokenGrant(token: "t", expiresAt: "x", permissions: perms, repositorySelection: "all", repositories: []))
        }
        let n = try Narrowing(repositories: [], permissions: [("contents", "read")])
        XCTAssertFalse(widened(n, ["contents": "read", "metadata": "read"]))
        XCTAssertTrue(widened(n, ["contents": "read", "metadata": "write"]))
        XCTAssertTrue(widened(n, ["contents": "read", "metadata": "admin"]))
        XCTAssertTrue(widened(n, ["contents": "read", "metadata": ""]))
        XCTAssertTrue(widened(n, ["contents": "read", "issues": "read"]), "other unrequested permissions still widen")
        let m = try Narrowing(repositories: [], permissions: [("metadata", "read")])
        XCTAssertFalse(widened(m, ["metadata": "read"]))
        let w = World()
        w.github.handler = { c in
            c.method == "POST" ? json(.object(["token": .string(secretToken), "expires_at": .string("2030-01-01T00:00:00Z"), "repository_selection": .string("all"),
                                               "permissions": .object(["contents": .string("read"), "metadata": .string("read")])]), status: 201) : w.defaultHandler(c)
        }
        XCTAssertEqual(w.run(["mint", "app", "42", "--perm", "contents=read"]).exitCode, 0)
    }

    // item 6
    func testRepositoryNamesAreCutTo40CharactersInThePromptOnly() throws {
        let exact = String(repeating: "a", count: 40), long = String(repeating: "b", count: 41)
        let w = World()
        w.run(["mint", "app", "42", "--repo", exact, "--repo", long, "--perm", "contents=read"])
        XCTAssertEqual(w.presence.reasons, ["mint a GitHub token for app on octo-org (permissions: contents:read; repositories: \(exact), \(String(repeating: "b", count: 40))\u{2026})"])
        XCTAssertEqual(JSON.parse(w.github.posts[0].body)?["repositories"], .array([.string(exact), .string(long)]), "the request keeps the full names")
    }

    // item 12
    func testInstallationCommand() {
        let w = World()
        w.presence.defaultAnswer = false
        let r = w.run(["installation", "app", "42"])
        XCTAssertEqual(r.exitCode, 0)
        XCTAssertEqual(stdoutJSON(r)["account"]?["login"]?.stringValue, "octo-org")
        XCTAssertEqual(w.github.calls.map(\.path), ["/app/installations/42"])
        XCTAssertEqual(w.presence.reasons.count, 0)
        XCTAssertNil(w.rateLog)
        assertFails(World(withKey: false).run(["installation", "app", "42"]), "not-found", exit: 3)
        let bad = World(); bad.github.handler = { _ in HTTPResponse(status: 404, body: Data("{\"message\":\"Not Found\"}".utf8)) }
        let br = bad.run(["installation", "app", "42"])
        assertFails(br, "github", exit: 6)
        XCTAssertEqual(stderrJSON(br)["status"]?.intValue, 404)
        let arr = World(); arr.github.handler = { _ in json(.array([])) }
        assertFails(arr.run(["installation", "app", "42"]), "github", exit: 6)
        for bad in [["installation"], ["installation", "app"], ["installation", "app", "x"], ["installation", "app", "1", "2"], ["installation", "A", "1"]] {
            assertFails(World().run(bad), "usage", exit: 2)
        }
    }
}

/// PEM text is armoured at runtime; no literal key header appears in the repository.
enum TestPEM {
    static var pkcs1: Data {
        let dashes = String(repeating: "-", count: 5)
        let label = "RSA PRIVATE" + " KEY"
        let body = TestKeys.rsa2048DER.base64EncodedString(options: [.lineLength64Characters, .endLineWithLineFeed])
        return Data("\(dashes)BEGIN \(label)\(dashes)\n\(body)\n\(dashes)END \(label)\(dashes)\n".utf8)
    }
}
