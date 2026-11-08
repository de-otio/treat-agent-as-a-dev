import XCTest
import Security
@testable import SignerCore

final class JWTTests: XCTestCase {
    func testHeaderAndPayloadBytesAreExactForAFixedClock() {
        XCTAssertEqual(JWT.headerJSON, "{\"alg\":\"RS256\",\"typ\":\"JWT\"}")
        XCTAssertEqual(JWT.payloadJSON(appId: 123, now: 1_700_000_000), "{\"iat\":1699999940,\"exp\":1700000120,\"iss\":\"123\"}")
        XCTAssertEqual(JWT.signingInput(appId: 123, now: 1_700_000_000),
                       "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJpYXQiOjE2OTk5OTk5NDAsImV4cCI6MTcwMDAwMDEyMCwiaXNzIjoiMTIzIn0")
    }

    func testSignatureVerifiesWithThePublicKey() throws {
        let key = TestKeys.rsa2048
        let jwt = try JWT.make(appId: 123, now: 1_700_000_000, key: SecKeySigningKey(key: key))
        let parts = jwt.split(separator: ".", omittingEmptySubsequences: false).map(String.init)
        XCTAssertEqual(parts.count, 3)
        XCTAssertFalse(jwt.contains("=") || jwt.contains("+") || jwt.contains("/"))
        func decode(_ s: String) -> Data {
            var t = s.replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
            while t.count % 4 != 0 { t += "=" }
            return Data(base64Encoded: t)!
        }
        let pub = SecKeyCopyPublicKey(key)!
        let verify = { (input: String) -> Bool in
            SecKeyVerifySignature(pub, .rsaSignatureMessagePKCS1v15SHA256, Data(input.utf8) as CFData, decode(parts[2]) as CFData, nil)
        }
        XCTAssertTrue(verify(parts[0] + "." + parts[1]))
        XCTAssertFalse(verify(parts[0] + "." + JWT.base64url(Data(JWT.payloadJSON(appId: 124, now: 1_700_000_000).utf8))), "a different payload must not verify")
    }

    func testBase64URLHasNoPaddingOrUnsafeCharacters() {
        XCTAssertEqual(JWT.base64url(Data([0xFB, 0xFF, 0xFE])), "-__-")
        XCTAssertEqual(JWT.base64url(Data([0x01])), "AQ")
    }
}

final class NarrowingTests: XCTestCase {
    func testCanonicalJSONAndHash() throws {
        let n = try Narrowing(repositories: ["B", "a", "A"], permissions: [("issues", "write"), ("contents", "read")])
        XCTAssertEqual(n.canonicalJSON, "{\"permissions\":{\"contents\":\"read\",\"issues\":\"write\"},\"repositories\":[\"a\",\"b\"]}")
        XCTAssertEqual(n.hash, "3802654dfdca1598e7e1d4c155c2cdf0216694fc91ad5313091d7d7ceef53c17")
        XCTAssertEqual(Narrowing.none.canonicalJSON, "{\"permissions\":{},\"repositories\":[]}")
        XCTAssertEqual(Narrowing.none.hash, "a64a5b52a144e0672090a27261e85d88fbdd650976b043c70f2d3b60fff7f458")
        XCTAssertEqual(n.repositories, ["B", "a"], "first spelling kept, case-insensitive dedupe")
    }

    func testHashIsOrderAndCaseIndependentAndSensitiveToContent() throws {
        var rng = SplitMix64(seed: 42)
        let names = ["x", "y", "Z", "w", "v"]
        for _ in 0..<50 {
            let a = Array(names.shuffled(using: &rng).prefix(3))
            let b = a.shuffled(using: &rng).map { $0.uppercased() }
            XCTAssertEqual(try Narrowing(repositories: a, permissions: []).hash, try Narrowing(repositories: b, permissions: []).hash)
        }
        let base = try Narrowing(repositories: ["a"], permissions: [("contents", "read")])
        XCTAssertNotEqual(base.hash, try Narrowing(repositories: ["b"], permissions: [("contents", "read")]).hash)
        XCTAssertNotEqual(base.hash, try Narrowing(repositories: ["a"], permissions: [("contents", "write")]).hash)
        XCTAssertNotEqual(base.hash, try Narrowing(repositories: ["a"], permissions: []).hash)
        // '_' sorts before letters bytewise
        let p = try Narrowing(repositories: [], permissions: [("ab", "read"), ("a_b", "read")])
        XCTAssertTrue(p.canonicalJSON.contains("\"a_b\":\"read\",\"ab\":\"read\""))
        XCTAssertEqual(p.hash.count, 64)
        XCTAssertEqual(p.hash, p.hash.lowercased())
    }
}

final class ArgumentTests: XCTestCase {
    func usage(_ args: [String], file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertThrowsError(try Arguments.parse(args), "\(args)", file: file, line: line) {
            guard case .usage = $0 as? SignerError else { return XCTFail("\($0)", file: file, line: line) }
        }
    }

    func testSlugRules() throws {
        XCTAssertEqual(try Validate.slug("a"), "a")
        XCTAssertEqual(try Validate.slug("0-a-"), "0-a-")
        XCTAssertEqual(try Validate.slug(String(repeating: "a", count: 39)).count, 39)
        for bad in ["", "-a", "A", "a_b", "a b", "a/b", "a.b", String(repeating: "a", count: 40), "a\n", "é", "../x", "a\0"] {
            XCTAssertThrowsError(try Validate.slug(bad), bad)
        }
    }

    func testIntegerRules() throws {
        XCTAssertEqual(try Validate.positiveInt("1", what: "x"), 1)
        XCTAssertEqual(try Validate.positiveInt("9007199254740992", what: "x"), 9_007_199_254_740_992)
        for bad in ["0", "-1", "+1", "01", "9007199254740993", "99999999999999999999", "1.5", "1e3", " 1", "", "0x10", "١٢"] {
            XCTAssertThrowsError(try Validate.positiveInt(bad, what: "x"), bad)
        }
    }

    func testRepoAndPermissionRules() throws {
        _ = try Validate.repoName("a.B_c-1"); _ = try Validate.repoName(String(repeating: "a", count: 100))
        for bad in ["", "a/b", "a b", String(repeating: "a", count: 101), "a\n", "é"] { XCTAssertThrowsError(try Validate.repoName(bad), bad) }
        let p = try Validate.permission("pull_requests=admin"); XCTAssertEqual(p.name, "pull_requests"); XCTAssertEqual(p.level, "admin")
        for bad in ["contents", "contents=", "=read", "Contents=read", "_a=read", "a1=read", "a=Read", "a=none", "a=read=write", String(repeating: "a", count: 65) + "=read"] {
            XCTAssertThrowsError(try Validate.permission(bad), bad)
        }
        _ = try Validate.permission(String(repeating: "a", count: 64) + "=read")
    }

    func testCommandParsing() throws {
        XCTAssertEqual(try Arguments.parse(["version"]), .version)
        XCTAssertEqual(try Arguments.parse(["status", "app"]), .status(slug: "app"))
        XCTAssertEqual(try Arguments.parse(["import", "app", "7", "--replace"]), .importKey(slug: "app", appId: 7, replace: true))
        XCTAssertEqual(try Arguments.parse(["import", "--replace", "app", "7"]), .importKey(slug: "app", appId: 7, replace: true))
        XCTAssertEqual(try Arguments.parse(["import", "app", "7"]), .importKey(slug: "app", appId: 7, replace: false))
        guard case .mint(_, _, let n) = try Arguments.parse(["mint", "app", "1", "--repo", "A", "--repo", "a", "--perm", "x=read"]) else { return XCTFail() }
        XCTAssertEqual(n.repositories, ["A"]); XCTAssertEqual(n.permissions, ["x": "read"])
    }

    func testUsageErrors() {
        usage([]); usage(["nope"]); usage(["version", "x"]); usage(["status"]); usage(["status", "a", "b"]); usage(["status", "A"])
        usage(["delete"]); usage(["app-meta", "a", "--x"]); usage(["installations"])
        usage(["import", "a"]); usage(["import", "a", "1", "2"]); usage(["import", "a", "1", "--replace", "--replace"]); usage(["import", "a", "1", "--repo", "x"])
        usage(["mint", "a"]); usage(["mint", "a", "x"]); usage(["mint", "a", "1", "--repo"]); usage(["mint", "a", "1", "--repo", "--perm"])
        usage(["mint", "a", "1", "--perm", "x=read", "--perm", "x=write"]); usage(["mint", "a", "1", "--perm", "x=read", "--perm", "x=read"])
        usage(["mint", "a", "1", "--replace"]); usage(["mint", "a", "1", "--bogus"]); usage(["mint", "a", "1", "extra"])
        usage(["mint", "a", "1"] + (0..<101).flatMap { ["--repo", "r\($0)"] })
        XCTAssertNoThrow(try Arguments.parse(["mint", "a", "1"] + (0..<100).flatMap { ["--repo", "r\($0)"] }))
        XCTAssertNoThrow(try Arguments.parse(["mint", "a", "1"] + (0..<150).flatMap { _ in ["--repo", "same"] }), "dedupe happens before the cap")
    }
}

final class ReasonTests: XCTestCase {
    func testReasons() throws {
        XCTAssertEqual(Reasons.importKey(slug: "s", appId: 5), "import the GitHub App private key for s (App ID 5)")
        XCTAssertEqual(Reasons.replaceKey(slug: "s", appId: 5), "replace the GitHub App private key for s (App ID 5)")
        XCTAssertEqual(Reasons.deleteKey(slug: "s"), "delete the GitHub App private key for s")
        XCTAssertEqual(Reasons.mint(slug: "s", account: "o", narrowing: .none),
                       "mint a GitHub token for s on o (permissions: all the App was granted; repositories: all the App can reach)")
        let few = try Narrowing(repositories: ["b", "a"], permissions: [("issues", "write"), ("contents", "read")])
        XCTAssertEqual(Reasons.mint(slug: "s", account: "o", narrowing: few),
                       "mint a GitHub token for s on o (permissions: contents:read, issues:write; repositories: a, b)")
        let five = try Narrowing(repositories: ["a", "b", "c", "d", "e"], permissions: [])
        XCTAssertTrue(Reasons.mint(slug: "s", account: "o", narrowing: five).contains("a, b, c, d, e)"))
        let six = try Narrowing(repositories: ["a", "b", "c", "d", "e", "f"], permissions: [])
        XCTAssertTrue(Reasons.mint(slug: "s", account: "o", narrowing: six).contains("a, b, c, d, e and 1 more)"))
        let perms = try Narrowing(repositories: [], permissions: ["a", "b", "c", "d", "e", "f", "g"].map { ($0, "read") })
        XCTAssertTrue(Reasons.mint(slug: "s", account: "o", narrowing: perms).contains("a:read, b:read, c:read, d:read, e:read and 2 more; repositories:"))
    }
}

final class OtherCommandTests: XCTestCase {
    func testVersion() {
        let w = World(withKey: false)
        let r = w.run(["version"])
        XCTAssertEqual(r.stdout, "{\"identifier\":\"org.de-otio.taaad-signer\",\"version\":\"0.1.0\"}\n")
        XCTAssertEqual(r.stderr, "")
        XCTAssertTrue(w.github.calls.isEmpty)
    }

    func testStatus() {
        XCTAssertEqual(stdoutJSON(World().run(["status", "app"])), .object(["slug": .string("app"), "present": .bool(true), "app_id": .int(123)]))
        XCTAssertEqual(stdoutJSON(World(withKey: false).run(["status", "app"])), .object(["slug": .string("app"), "present": .bool(false), "app_id": .null]))
        let w = World(); w.presence.defaultAnswer = false
        XCTAssertEqual(w.run(["status", "app"]).exitCode, 0)
        XCTAssertEqual(w.presence.reasons.count, 0, "status never prompts")
    }

    func testAppMetaAndInstallationsPassThroughWithoutPrompting() {
        let w = World()
        XCTAssertEqual(stdoutJSON(w.run(["app-meta", "app"]))["slug"]?.stringValue, "my-app")
        XCTAssertEqual(stdoutJSON(w.run(["installations", "app"])).arrayValue?.count, 1)
        XCTAssertEqual(w.github.calls.map(\.path), ["/app", "/app/installations?per_page=100"])
        XCTAssertEqual(w.presence.reasons.count, 0)
        assertFails(World(withKey: false).run(["app-meta", "app"]), "not-found", exit: 3)
        let bad = World(); bad.github.handler = { _ in json(.array([])) }
        assertFails(bad.run(["app-meta", "app"]), "github", exit: 6)
    }

    func testDeleteRemovesEverythingForTheSlugOnly() {
        let w = World()
        w.store.items[StoreKey(kind: .token, account: "app/42/aa")] = Data("{}".utf8)
        w.store.items[StoreKey(kind: .token, account: "app/43/bb")] = Data("{}".utf8)
        w.store.items[StoreKey(kind: .token, account: "app2/42/cc")] = Data("{}".utf8)
        w.store.items[StoreKey(kind: .rate, account: "app")] = RateLimit.encode([1])
        let r = w.run(["delete", "app"])
        XCTAssertEqual(stdoutJSON(r), .object(["slug": .string("app"), "deleted": .bool(true)]))
        XCTAssertEqual(w.presence.reasons, ["delete the GitHub App private key for app"])
        XCTAssertNil(w.keys.keys["app"])
        XCTAssertEqual(w.store.accounts(.token), ["app2/42/cc"])
        XCTAssertTrue(w.store.accounts(.meta).isEmpty)
        XCTAssertEqual(w.rateLog, [w.now], "delete spends a prompt and does not reset the budget")
    }

    func testDeleteNeedsPresenceAndAKey() {
        let w = World(); w.presence.defaultAnswer = false
        assertFails(w.run(["delete", "app"]), "presence-denied", exit: 4)
        XCTAssertNotNil(w.keys.keys["app"])
        XCTAssertFalse(w.trace.events.contains { $0.hasPrefix("store.delete") || $0 == "keys.delete" })
        assertFails(World(withKey: false).run(["delete", "app"]), "not-found", exit: 3)
    }
}

final class OutputTests: XCTestCase {
    func testErrorPathsNeverWriteStdoutAndTokensNeverReachStderr() {
        var cases: [(String, ExecResult)] = []
        func add(_ name: String, _ r: ExecResult) { cases.append((name, r)) }
        let mint = ["mint", "app", "42", "--repo", "r"]
        add("usage", World().run(["bogus"]))
        add("not-found", World(withKey: false).run(mint))
        let denied = World(); denied.presence.defaultAnswer = false; add("denied", denied.run(mint))
        let limited = World(); limited.store.items[StoreKey(kind: .rate, account: "app")] = Data("x".utf8); add("limited", limited.run(mint))
        let gh = World(); gh.github.handler = { _ in HTTPResponse(status: 500, body: Data("{\"message\":\"boom \(secretToken)\"}".utf8)) }; add("github", gh.run(mint))
        let wide = World()
        wide.github.handler = { c in
            c.method == "POST" ? json(.object(["token": .string(secretToken), "expires_at": .string("2030-01-01T00:00:00Z"),
                                               "repository_selection": .string("all"), "permissions": .object([:])]), status: 201) : wide.defaultHandler(c)
        }
        add("widened", wide.run(mint))
        let kc = World(); kc.keys.ambiguous = ["app"]; add("keychain", kc.run(mint))
        add("exists", World().run(["import", "app", "1"], stdin: pem("RSA PRIVATE KEY", TestKeys.rsa2048DER)))
        add("bad-key", World(withKey: false).run(["import", "app", "1"], stdin: Data("x".utf8)))
        let mm = World(withKey: false); mm.appId = 2; add("app-mismatch", mm.run(["import", "app", "1"], stdin: pem("RSA PRIVATE KEY", TestKeys.rsa2048DER)))
        for (name, r) in cases {
            XCTAssertNotEqual(r.exitCode, 0, name)
            XCTAssertEqual(r.stdout, "", "\(name): stdout")
            XCTAssertEqual(r.stderr.filter { $0 == "\n" }.count, 1, name)
            XCTAssertTrue(r.stderr.hasSuffix("\n"), name)
            XCTAssertNotNil(JSON.parse(Data(r.stderr.utf8))?["error"]?.stringValue, name)
            XCTAssertNotNil(JSON.parse(Data(r.stderr.utf8))?["message"]?.stringValue, name)
            XCTAssertFalse(r.stderr.contains("ghs_"), "\(name): token on stderr")
        }
        XCTAssertEqual(cases.map { JSON.parse(Data($0.1.stderr.utf8))?["error"]?.stringValue ?? "" },
                       ["usage", "not-found", "presence-denied", "rate-limited", "github", "widened", "keychain", "exists", "bad-key", "app-mismatch"])
        XCTAssertEqual(cases.map { Int($0.1.exitCode) }, [2, 3, 4, 5, 6, 7, 8, 9, 10, 11])
    }

    func testSuccessWritesOneLineToStdoutAndNothingToStderr() {
        let r = World().run(["mint", "app", "42"])
        XCTAssertEqual(r.exitCode, 0); XCTAssertEqual(r.stderr, "")
        XCTAssertEqual(r.stdout.filter { $0 == "\n" }.count, 1)
    }

    func testUnexpectedErrorsAreInternalExit1WithoutDetail() {
        struct Boom: Error, CustomStringConvertible { var description: String { "secret detail \(secretToken)" } }
        let w = World(); w.github.handler = { _ in throw Boom() }
        let r = w.run(["mint", "app", "42"])
        assertFails(r, "internal", exit: 1)
        XCTAssertFalse(r.stderr.contains("secret detail"))
    }

    func testGithubErrorMessageStripsControlCharactersAndCapsLength() {
        let w = World()
        let nasty = "bad\u{1B}[31m" + String(repeating: "x", count: 500)
        w.github.handler = { _ in HTTPResponse(status: 403, body: JSON.object(["message": .string(nasty)]).serializedData()) }
        let r = w.run(["app-meta", "app"])
        let m = stderrJSON(r)["message"]?.stringValue ?? ""
        XCTAssertFalse(m.contains("\u{1B}"))
        XCTAssertLessThan(m.count, 260)
        XCTAssertEqual(stderrJSON(r)["status"]?.intValue, 403)
    }

    func testRecordRoundTrips() {
        let meta = MetaRecord(slug: "s", appId: 3, appSlug: "a", importedAt: "2026-01-01T00:00:00Z")
        XCTAssertEqual(MetaRecord.parse(meta.json.serializedData()), meta)
        XCTAssertNil(MetaRecord.parse(Data("{\"v\":2}".utf8)))
        let g = TokenGrant(token: "t", expiresAt: "2030-01-01T00:00:00Z", permissions: ["a": "read"], repositorySelection: "selected", repositories: ["r"])
        XCTAssertEqual(CachedToken.parse(CachedToken(grant: g, account: "o").json.serializedData()), CachedToken(grant: g, account: "o"))
        XCTAssertNil(CachedToken.parse(Data("{\"v\":1,\"token\":\"t\"}".utf8)))
    }
}
