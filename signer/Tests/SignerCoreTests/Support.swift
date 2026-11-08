import Foundation
import Security
import XCTest
@testable import SignerCore

// MARK: fakes

/// Serialises fake state so tests can drive them from several threads.
final class Guard: @unchecked Sendable {
    private let l = NSRecursiveLock()
    func run<T>(_ f: () throws -> T) rethrows -> T { l.lock(); defer { l.unlock() }; return try f() }
}

final class Trace {
    private let g = Guard()
    private var _events: [String] = []
    var events: [String] { get { g.run { _events } } set { g.run { _events = newValue } } }
    func add(_ e: String) { g.run { _events.append(e) } }
}

/// Test lock: a real mutex, so concurrent runs behave as under flock.
final class MutexLock: Lock, @unchecked Sendable {
    private let m = NSLock()
    func withExclusive<T>(_ body: () throws -> T) throws -> T { m.lock(); defer { m.unlock() }; return try body() }
}

/// Negative control: no serialisation at all.
struct NoLock: Lock {
    func withExclusive<T>(_ body: () throws -> T) throws -> T { try body() }
}

final class FakeClock: Clock {
    var date: Date
    init(_ iso: String = "2026-11-08T12:00:00Z") { date = Time.parseISO8601(iso)! }
    func now() -> Date { date }
    func advance(_ s: TimeInterval) { date = date.addingTimeInterval(s) }
}

struct StoreKey: Hashable { let kind: ItemKind; let account: String }

final class MemStore: Store {
    private let g = Guard()
    private var _items: [StoreKey: Data] = [:]
    var items: [StoreKey: Data] { get { g.run { _items } } set { g.run { _items = newValue } } }
    let trace: Trace
    var failPut: Set<ItemKind> = []
    /// Runs after every `get(.rate)` read, outside the guard: widens the check-then-write race.
    var afterRateRead: (() -> Void)?
    init(trace: Trace) { self.trace = trace }
    func get(_ kind: ItemKind, account: String) throws -> Data? {
        let d = g.run { _items[StoreKey(kind: kind, account: account)] }
        if kind == .rate { afterRateRead?() }
        return d
    }
    func put(_ kind: ItemKind, account: String, data: Data) throws {
        trace.add("store.put.\(kind.rawValue)")
        if failPut.contains(kind) { throw SignerError.keychain(osstatus: -25299, message: "fake put failure") }
        g.run { _items[StoreKey(kind: kind, account: account)] = data }
    }
    func delete(_ kind: ItemKind, account: String) throws {
        trace.add("store.delete.\(kind.rawValue)")
        g.run { _items[StoreKey(kind: kind, account: account)] = nil }
    }
    func list(_ kind: ItemKind, accountPrefix: String) throws -> [String] {
        items.keys.filter { $0.kind == kind && $0.account.hasPrefix(accountPrefix) }.map(\.account).sorted()
    }
    func accounts(_ kind: ItemKind) -> [String] { items.keys.filter { $0.kind == kind }.map(\.account).sorted() }
}

final class FakeKey: SigningKey {
    let der: Data
    init(der: Data) { self.der = der }
    func sign(_ message: Data) throws -> Data { Data("fake-signature".utf8) }
}

final class FakeKeys: KeyStore {
    var keys: [String: FakeKey] = [:]
    var ambiguous: Set<String> = []
    var failAdd = false
    var made: [Data] = []
    let trace: Trace
    init(trace: Trace) { self.trace = trace }
    func makeKey(pkcs1DER: Data) throws -> any SigningKey { made.append(pkcs1DER); return FakeKey(der: pkcs1DER) }
    func add(_ key: any SigningKey, slug: String) throws {
        trace.add("keys.add")
        if failAdd { throw SignerError.keychain(osstatus: -25299, message: "fake add failure") }
        keys[slug] = (key as! FakeKey)
    }
    func find(slug: String) throws -> (any SigningKey)? {
        if ambiguous.contains(slug) { throw SignerError.keychain(osstatus: -25299, message: "ambiguous") }
        return keys[slug]
    }
    func delete(slug: String) throws { trace.add("keys.delete"); keys[slug] = nil }
}

final class FakePresence: Presence {
    private let g = Guard()
    var answers: [Bool] = []
    var defaultAnswer = true
    private var _reasons: [String] = []
    var reasons: [String] { g.run { _reasons } }
    var onApprove: (() -> Void)?
    func approve(reason: String) -> Bool {
        g.run { _reasons.append(reason) }
        onApprove?()
        return g.run { answers.isEmpty ? defaultAnswer : answers.removeFirst() }
    }
}

struct Call: Equatable { let method: String; let path: String; let jwt: String; let body: Data }

final class FakeGitHub: GitHub {
    private let g = Guard()
    private var _calls: [Call] = []
    var calls: [Call] { g.run { _calls } }
    var handler: (Call) throws -> HTTPResponse = { _ in HTTPResponse(status: 500, body: Data()) }
    let trace: Trace
    init(trace: Trace) { self.trace = trace }
    func get(path: String, jwt: String) throws -> HTTPResponse { try handle(Call(method: "GET", path: path, jwt: jwt, body: Data())) }
    func post(path: String, jwt: String, body: Data) throws -> HTTPResponse { try handle(Call(method: "POST", path: path, jwt: jwt, body: body)) }
    private func handle(_ c: Call) throws -> HTTPResponse {
        g.run { _calls.append(c) }; trace.add("github.\(c.method) \(c.path)")
        return try handler(c)
    }
    var posts: [Call] { calls.filter { $0.method == "POST" } }
}

func json(_ o: JSON, status: Int = 200) -> HTTPResponse { HTTPResponse(status: status, body: o.serializedData()) }

// MARK: world

let secretToken = "ghs_SECRET_TOKEN_VALUE_123"

final class World {
    let trace = Trace()
    let clock = FakeClock()
    let store: MemStore
    let keys: FakeKeys
    let presence = FakePresence()
    let github: FakeGitHub
    /// Seconds from "now" at which minted tokens expire.
    var tokenLifetime = 3600
    var appId = 123
    var stdinCalls = 0
    var lock: any Lock = MutexLock()

    init(withKey: Bool = true, slug: String = "app") {
        store = MemStore(trace: trace); keys = FakeKeys(trace: trace); github = FakeGitHub(trace: trace)
        if withKey {
            keys.keys[slug] = FakeKey(der: Data([1, 2, 3]))
            let meta = MetaRecord(slug: slug, appId: appId, appSlug: "my-app", importedAt: "2026-01-01T00:00:00Z")
            store.items[StoreKey(kind: .meta, account: slug)] = meta.json.serializedData()
        }
        github.handler = { [unowned self] c in self.defaultHandler(c) }
    }

    var env: Environment { Environment(store: store, keys: keys, clock: clock, presence: presence, github: github, lock: lock) }

    /// Default GitHub: installation 42 on "octo-org"; tokens echo the narrowing exactly.
    func defaultHandler(_ c: Call) -> HTTPResponse {
        if c.method == "GET", c.path == "/app" { return json(.object(["id": .int(appId), "slug": .string("my-app")])) }
        if c.method == "GET", c.path.hasPrefix("/app/installations/") {
            return json(.object(["id": .int(42), "account": .object(["login": .string("octo-org")])]))
        }
        if c.method == "GET", c.path.hasPrefix("/app/installations?") {
            return json(.array([.object(["id": .int(42)])]))
        }
        if c.method == "POST", c.path.hasSuffix("/access_tokens") {
            let req = JSON.parse(c.body) ?? .object([:])
            var o: [String: JSON] = [
                "token": .string(secretToken),
                "expires_at": .string(Time.formatISO8601(clock.now().addingTimeInterval(TimeInterval(tokenLifetime)))),
                "repository_selection": .string(req["repositories"] == nil ? "all" : "selected"),
                "permissions": req["permissions"] ?? .object(["contents": .string("read")]),
            ]
            if let r = req["repositories"]?.arrayValue { o["repositories"] = .array(r.map { .object(["name": $0]) }) }
            return json(.object(o), status: 201)
        }
        return HTTPResponse(status: 404, body: Data("{\"message\":\"Not Found\"}".utf8))
    }

    @discardableResult
    func run(_ args: [String], stdin: Data = Data()) -> ExecResult {
        SignerCore.run(arguments: args, readStdin: { [unowned self] in self.stdinCalls += 1; return stdin }, environment: env)
    }

    var rateLog: [Int]? {
        guard let d = store.items[StoreKey(kind: .rate, account: "app")], let j = JSON.parse(d) else { return nil }
        return j["prompts"]?.arrayValue?.compactMap(\.intValue)
    }
    var now: Int { clock.unixSeconds() }
}

func stdoutJSON(_ r: ExecResult, file: StaticString = #filePath, line: UInt = #line) -> JSON {
    XCTAssertTrue(r.stdout.hasSuffix("\n"), file: file, line: line)
    XCTAssertEqual(r.stdout.filter { $0 == "\n" }.count, 1, "stdout must be one line", file: file, line: line)
    return JSON.parse(Data(r.stdout.utf8)) ?? .null
}

func stderrJSON(_ r: ExecResult, file: StaticString = #filePath, line: UInt = #line) -> JSON {
    XCTAssertEqual(r.stderr.filter { $0 == "\n" }.count, 1, "stderr must be one line", file: file, line: line)
    return JSON.parse(Data(r.stderr.utf8)) ?? .null
}

func assertFails(_ r: ExecResult, _ code: String, exit: Int32, file: StaticString = #filePath, line: UInt = #line) {
    XCTAssertEqual(r.exitCode, exit, file: file, line: line)
    XCTAssertEqual(r.stdout, "", "no error path may write to stdout", file: file, line: line)
    XCTAssertEqual(stderrJSON(r, file: file, line: line)["error"]?.stringValue, code, file: file, line: line)
}

struct SplitMix64: RandomNumberGenerator {
    var state: UInt64
    init(seed: UInt64) { state = seed }
    mutating func next() -> UInt64 {
        state &+= 0x9E37_79B9_7F4A_7C15
        var z = state
        z = (z ^ (z >> 30)) &* 0xBF58_476D_1CE4_E5B9
        z = (z ^ (z >> 27)) &* 0x94D0_49BB_1331_11EB
        return z ^ (z >> 31)
    }
}

// MARK: real throwaway RSA keys (never stored)

func makeRSAKey(bits: Int) -> SecKey {
    var e: Unmanaged<CFError>?
    let k = SecKeyCreateRandomKey([kSecAttrKeyType: kSecAttrKeyTypeRSA, kSecAttrKeySizeInBits: bits,
                                   kSecAttrIsPermanent: false] as CFDictionary, &e)
    return k!
}

func pkcs1DER(_ key: SecKey) -> Data {
    var e: Unmanaged<CFError>?
    return SecKeyCopyExternalRepresentation(key, &e)! as Data
}

struct SecKeySigningKey: SigningKey {
    let key: SecKey
    func sign(_ message: Data) throws -> Data {
        var e: Unmanaged<CFError>?
        return SecKeyCreateSignature(key, .rsaSignatureMessagePKCS1v15SHA256, message as CFData, &e)! as Data
    }
}

func derLength(_ n: Int) -> [UInt8] {
    if n < 0x80 { return [UInt8(n)] }
    if n < 0x100 { return [0x81, UInt8(n)] }
    return [0x82, UInt8(n >> 8), UInt8(n & 0xFF)]
}
func derWrap(_ tag: UInt8, _ content: [UInt8]) -> [UInt8] { [tag] + derLength(content.count) + content }

func pkcs8(oid: [UInt8], params: [UInt8] = [0x05, 0x00], inner: Data) -> Data {
    let alg = derWrap(0x30, derWrap(0x06, oid) + params)
    return Data(derWrap(0x30, [0x02, 0x01, 0x00] + alg + derWrap(0x04, [UInt8](inner))))
}
let rsaOID: [UInt8] = [0x2A, 0x86, 0x48, 0x86, 0xF7, 0x0D, 0x01, 0x01, 0x01]

func pem(_ label: String, _ der: Data, extraHeader: String? = nil) -> Data {
    var s = "-----BEGIN \(label)-----\n"
    if let h = extraHeader { s += h + "\n\n" }
    s += der.base64EncodedString(options: [.lineLength64Characters, .endLineWithLineFeed]) + "\n-----END \(label)-----\n"
    return Data(s.utf8)
}

/// Generated once: RSA key generation is slow.
enum TestKeys {
    nonisolated(unsafe) static let rsa2048 = makeRSAKey(bits: 2048)
    static let rsa2048DER = pkcs1DER(rsa2048)
    static let rsa1024DER = pkcs1DER(makeRSAKey(bits: 1024))
}
