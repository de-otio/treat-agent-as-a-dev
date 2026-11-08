import Foundation

public protocol Clock {
    func now() -> Date
}

extension Clock {
    public func unixSeconds() -> Int { Int(now().timeIntervalSince1970.rounded(.down)) }
}

/// The three generic-password item kinds. The key itself lives behind `KeyStore`.
public enum ItemKind: String, Sendable, CaseIterable {
    case meta = "taaad-signer.meta"
    case token = "taaad-signer.token"
    case rate = "taaad-signer.rate"
}

public protocol Store {
    /// nil when absent.
    func get(_ kind: ItemKind, account: String) throws -> Data?
    /// Creates or replaces.
    func put(_ kind: ItemKind, account: String, data: Data) throws
    /// Idempotent: absent is success.
    func delete(_ kind: ItemKind, account: String) throws
    /// Accounts of this kind starting with `accountPrefix`.
    func list(_ kind: ItemKind, accountPrefix: String) throws -> [String]
}

/// An opaque private key that can sign and nothing else. There is deliberately no export.
public protocol SigningKey {
    /// RSASSA-PKCS1-v1_5 with SHA-256.
    func sign(_ message: Data) throws -> Data
}

public protocol KeyStore {
    /// Builds an in-memory key from PKCS#1 DER. Nothing is stored.
    func makeKey(pkcs1DER: Data) throws -> any SigningKey
    func add(_ key: any SigningKey, slug: String) throws
    /// nil when absent; an ambiguous match throws `SignerError.keychain`.
    func find(slug: String) throws -> (any SigningKey)?
    /// Idempotent.
    func delete(slug: String) throws
}

public protocol Presence {
    /// True only when the person approved. Unavailable presence is false.
    func approve(reason: String) -> Bool
}

public struct HTTPResponse: Equatable, Sendable {
    public let status: Int
    public let body: Data
    public init(status: Int, body: Data) { self.status = status; self.body = body }
}

public protocol GitHub {
    /// Transport failures throw `SignerError.github(status: 0, ...)`.
    func get(path: String, jwt: String) throws -> HTTPResponse
    func post(path: String, jwt: String, body: Data) throws -> HTTPResponse
}

public struct Environment {
    public let store: any Store
    public let keys: any KeyStore
    public let clock: any Clock
    public let presence: any Presence
    public let github: any GitHub
    public init(store: any Store, keys: any KeyStore, clock: any Clock, presence: any Presence, github: any GitHub) {
        self.store = store; self.keys = keys; self.clock = clock; self.presence = presence; self.github = github
    }
}
