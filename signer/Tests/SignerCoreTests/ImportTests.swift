import XCTest
@testable import SignerCore

final class ImportTests: XCTestCase {
    let der = TestKeys.rsa2048DER
    var pkcs1PEM: Data { pem("RSA PRIVATE KEY", der) }
    var pkcs8PEM: Data { pem("PRIVATE KEY", pkcs8(oid: rsaOID, inner: der)) }

    // MARK: PEM parsing

    func testPKCS1AndPKCS8ParseToTheSameDER() throws {
        let a = try PEM.parsePrivateKey(pkcs1PEM)
        let b = try PEM.parsePrivateKey(pkcs8PEM)
        XCTAssertEqual(a, der)
        XCTAssertEqual(a, b)
        // CRLF line endings and surrounding blank lines are fine
        let crlf = Data(String(decoding: pkcs8PEM, as: UTF8.self).replacingOccurrences(of: "\n", with: "\r\n").utf8)
        XCTAssertEqual(try PEM.parsePrivateKey(Data("\n\n".utf8) + crlf + Data("\n".utf8)), der)
    }

    func assertBadKey(_ data: Data, _ why: String, file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertThrowsError(try PEM.parsePrivateKey(data), why, file: file, line: line) { e in
            guard case .badKey = e as? SignerError else { return XCTFail("\(why): got \(e)", file: file, line: line) }
        }
    }

    func testBadKeys() {
        assertBadKey(pem("RSA PRIVATE KEY", der, extraHeader: "Proc-Type: 4,ENCRYPTED\nDEK-Info: AES-128-CBC,00112233445566778899AABBCCDDEEFF"), "legacy encrypted")
        assertBadKey(pem("ENCRYPTED PRIVATE KEY", pkcs8(oid: rsaOID, inner: der)), "PKCS#8 encrypted")
        assertBadKey(pem("PRIVATE KEY", pkcs8(oid: [0x2A, 0x86, 0x48, 0xCE, 0x3D, 0x02, 0x01],
                                              params: [0x06, 0x08, 0x2A, 0x86, 0x48, 0xCE, 0x3D, 0x03, 0x01, 0x07],
                                              inner: Data([0x30, 0x03, 0x02, 0x01, 0x01]))), "EC in PKCS#8")
        assertBadKey(pem("EC PRIVATE KEY", Data([0x30, 0x03, 0x02, 0x01, 0x01])), "EC label")
        assertBadKey(pem("PUBLIC KEY", der), "public key label")
        assertBadKey(pem("RSA PRIVATE KEY", TestKeys.rsa1024DER), "1024-bit PKCS#1")
        assertBadKey(pem("PRIVATE KEY", pkcs8(oid: rsaOID, inner: TestKeys.rsa1024DER)), "1024-bit PKCS#8")
        assertBadKey(pkcs1PEM + pkcs1PEM, "two keys")
        assertBadKey(pkcs1PEM + pkcs8PEM, "two keys of different types")
        assertBadKey(Data(), "empty")
        assertBadKey(Data("hello".utf8), "no PEM")
        assertBadKey(Data("junk\n".utf8) + pkcs1PEM, "text before the block")
        assertBadKey(pkcs1PEM + Data("junk\n".utf8), "text after the block")
        assertBadKey(Data((armor("BEGIN") + "\nAAAA\n").utf8), "unterminated")
        assertBadKey(pem("RSA PRIVATE KEY", Data(der.dropLast())), "truncated DER")
        assertBadKey(pem("RSA PRIVATE KEY", der + Data([0])), "trailing DER bytes")
        assertBadKey(Data((armor("BEGIN") + "\n!!!!\n" + armor("END") + "\n").utf8), "bad base64")
        assertBadKey(Data(repeating: 0x41, count: PEM.maxInputBytes + 1), "over 64 KiB")
        assertBadKey(Data([0xFF, 0xFE, 0xFD]), "not UTF-8")
    }

    func testExactly2048BitsIsAccepted() throws {
        XCTAssertEqual(try PEM.validatePKCS1([UInt8](der)), 2048)
        XCTAssertEqual(try PEM.validatePKCS1([UInt8](TestKeys.rsa1024DER)), 1024)
    }

    // MARK: import flow

    func importWorld(withKey: Bool = false) -> World {
        let w = World(withKey: false)
        if withKey {
            w.keys.keys["app"] = FakeKey(der: Data([9]))
            w.store.items[StoreKey(kind: .meta, account: "app")] = MetaRecord(slug: "app", appId: 123, appSlug: "old", importedAt: "2025-01-01T00:00:00Z").json.serializedData()
            w.store.items[StoreKey(kind: .token, account: "app/42/abc")] = Data("{}".utf8)
            w.store.items[StoreKey(kind: .token, account: "other/42/abc")] = Data("{}".utf8)
            w.store.items[StoreKey(kind: .rate, account: "app")] = RateLimit.encode([1])
        }
        return w
    }

    func testImportStoresKeyAndMetaAfterVerification() throws {
        let w = importWorld()
        let r = w.run(["import", "app", "123"], stdin: pkcs8PEM)
        XCTAssertEqual(r.exitCode, 0, r.stderr)
        XCTAssertEqual(stdoutJSON(r), .object(["slug": .string("app"), "app_id": .int(123), "app_slug": .string("my-app"), "replaced": .bool(false)]))
        XCTAssertEqual(w.keys.keys["app"]?.der, der)
        let meta = try XCTUnwrap(MetaRecord.parse(try XCTUnwrap(w.store.items[StoreKey(kind: .meta, account: "app")])))
        XCTAssertEqual(meta.appId, 123); XCTAssertEqual(meta.appSlug, "my-app")
        XCTAssertEqual(meta.importedAt, "2026-11-08T12:00:00Z")
        XCTAssertEqual(w.presence.reasons, ["import the GitHub App private key for app (App ID 123)"])
        XCTAssertEqual(w.github.calls.map(\.path), ["/app"])
        XCTAssertFalse(r.stdout.contains("PRIVATE") || r.stderr.contains("PRIVATE"))
    }

    func testAppMismatchLeavesEverythingUnchanged() {
        for replace in [false, true] {
            let w = importWorld(withKey: replace)
            w.appId = 999 // GET /app answers with another id
            let itemsBefore = w.store.items.filter { $0.key.kind != .rate }
            let keysBefore = w.keys.keys.mapValues(\.der)
            let r = w.run(["import", "app", "123"] + (replace ? ["--replace"] : []), stdin: pkcs1PEM)
            assertFails(r, "app-mismatch", exit: 11)
            XCTAssertEqual(w.store.items.filter { $0.key.kind != .rate }, itemsBefore, "replace=\(replace)")
            XCTAssertEqual(w.keys.keys.mapValues(\.der), keysBefore)
            XCTAssertFalse(w.trace.events.contains { $0.hasPrefix("store.delete") || $0 == "keys.delete" || $0 == "keys.add" }, "\(w.trace.events)")
        }
    }

    func testReplaceDeletesOldItemsOnlyAfterVerification() throws {
        let w = importWorld(withKey: true)
        let r = w.run(["import", "app", "123", "--replace"], stdin: pkcs1PEM)
        XCTAssertEqual(r.exitCode, 0, r.stderr)
        XCTAssertEqual(stdoutJSON(r)["replaced"], .bool(true))
        let getIdx = try XCTUnwrap(w.trace.events.firstIndex(of: "github.GET /app"))
        let firstDelete = try XCTUnwrap(w.trace.events.firstIndex { $0.hasPrefix("store.delete") || $0 == "keys.delete" })
        let add = try XCTUnwrap(w.trace.events.firstIndex(of: "keys.add"))
        XCTAssertLessThan(getIdx, firstDelete)
        XCTAssertLessThan(firstDelete, add)
        XCTAssertEqual(w.presence.reasons, ["replace the GitHub App private key for app (App ID 123)"])
        XCTAssertEqual(w.keys.keys["app"]?.der, der)
        XCTAssertNil(w.store.items[StoreKey(kind: .token, account: "app/42/abc")], "slug's token cache cleared")
        XCTAssertNotNil(w.store.items[StoreKey(kind: .token, account: "other/42/abc")], "other slugs untouched")
        XCTAssertEqual(w.store.items[StoreKey(kind: .rate, account: "app")], RateLimit.encode([w.now]), "replace spends one prompt and must not reset the budget")
        XCTAssertEqual(MetaRecord.parse(w.store.items[StoreKey(kind: .meta, account: "app")]!)?.appId, 123)
    }

    func testExistsWithoutReplaceAndNoPromptOrParse() {
        let w = importWorld(withKey: true)
        let r = w.run(["import", "app", "123"], stdin: pkcs1PEM)
        assertFails(r, "exists", exit: 9)
        XCTAssertEqual(w.presence.reasons.count, 0)
        XCTAssertTrue(w.github.calls.isEmpty)
        XCTAssertTrue(w.keys.made.isEmpty)
    }

    func testBadKeyNeverPromptsOrTouchesGitHub() {
        let w = importWorld()
        assertFails(w.run(["import", "app", "123"], stdin: pkcs1PEM + pkcs1PEM), "bad-key", exit: 10)
        assertFails(w.run(["import", "app", "123"], stdin: pem("RSA PRIVATE KEY", TestKeys.rsa1024DER)), "bad-key", exit: 10)
        XCTAssertEqual(w.presence.reasons.count, 0)
        XCTAssertTrue(w.github.calls.isEmpty)
        XCTAssertTrue(w.keys.keys.isEmpty)
    }

    func testOversizeStdinIsBadKey() {
        let w = importWorld()
        assertFails(w.run(["import", "app", "123"], stdin: Data(repeating: 0x20, count: PEM.maxInputBytes + 1)), "bad-key", exit: 10)
    }

    func testDeniedPresenceStoresNothingAndSendsNothing() {
        let w = importWorld()
        w.presence.defaultAnswer = false
        assertFails(w.run(["import", "app", "123"], stdin: pkcs1PEM), "presence-denied", exit: 4)
        XCTAssertTrue(w.github.calls.isEmpty)
        XCTAssertTrue(w.keys.keys.isEmpty)
        XCTAssertEqual(w.store.accounts(.meta), []); XCTAssertEqual(w.store.accounts(.token), [])
    }

    func testAddFailureAfterReplaceDeleteIsKeychainError() {
        let w = importWorld(withKey: true)
        w.keys.failAdd = true
        assertFails(w.run(["import", "app", "123", "--replace"], stdin: pkcs1PEM), "keychain", exit: 8)
        XCTAssertNil(w.keys.keys["app"], "old key was deleted; the caller still holds the PEM file")
    }

    func testMetaWriteFailureRollsBackTheKey() {
        let w = importWorld()
        w.store.failPut = [.meta]
        assertFails(w.run(["import", "app", "123"], stdin: pkcs1PEM), "keychain", exit: 8)
        XCTAssertNil(w.keys.keys["app"])
    }

    func testGitHubRejectionOfNewKeyIsGithubErrorAndStoresNothing() {
        let w = importWorld()
        w.github.handler = { _ in HTTPResponse(status: 401, body: Data("{\"message\":\"A JSON web token could not be decoded\"}".utf8)) }
        let r = w.run(["import", "app", "123"], stdin: pkcs1PEM)
        assertFails(r, "github", exit: 6)
        XCTAssertTrue(w.keys.keys.isEmpty); XCTAssertEqual(w.store.accounts(.meta), []); XCTAssertEqual(w.store.accounts(.token), [])
    }

    func testStdinIsReadOnlyForImport() {
        let w = World()
        w.run(["status", "app"]); w.run(["version"]); w.run(["mint", "app", "42"]); w.run(["delete", "app"])
        XCTAssertEqual(w.stdinCalls, 0)
        w.run(["import", "app", "123"], stdin: pkcs1PEM)
        XCTAssertEqual(w.stdinCalls, 1)
    }
}

/// PEM armour line built at runtime, so the file never contains a literal key header.
private func armor(_ kind: String) -> String { String(repeating: "-", count: 5) + kind + " RSA PRIVATE KEY" + String(repeating: "-", count: 5) }
