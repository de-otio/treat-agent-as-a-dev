import Foundation
import Security
import SignerCore

/// Keychain adaptation only. All items: data-protection keychain, one access group, this-device-only.
enum KC {
    static let group = "U26DY5A9DU.org.de-otio.taaad-signer"

    static func query(_ extra: [CFString: Any]) -> CFDictionary {
        var q: [CFString: Any] = [kSecUseDataProtectionKeychain: true, kSecAttrAccessGroup: group]
        for (k, v) in extra { q[k] = v }
        return q as CFDictionary
    }

    static func error(_ status: OSStatus) -> SignerError {
        let text = (SecCopyErrorMessageString(status, nil) as String?) ?? "keychain error"
        return .keychain(osstatus: status, message: "keychain: \(text)")
    }

    static var storageAttributes: [CFString: Any] { [
        kSecAttrAccessible: kSecAttrAccessibleWhenUnlockedThisDeviceOnly,
        kSecAttrSynchronizable: false,
    ] }
}

struct KeychainStore: Store {
    private func match(_ kind: ItemKind, _ account: String?) -> [CFString: Any] {
        var q: [CFString: Any] = [kSecClass: kSecClassGenericPassword, kSecAttrService: kind.rawValue]
        if let account { q[kSecAttrAccount] = account }
        return q
    }

    func get(_ kind: ItemKind, account: String) throws -> Data? {
        var out: CFTypeRef?
        var q = match(kind, account)
        q[kSecReturnData] = true
        q[kSecMatchLimit] = kSecMatchLimitOne
        let s = SecItemCopyMatching(KC.query(q), &out)
        if s == errSecItemNotFound { return nil }
        guard s == errSecSuccess, let d = out as? Data else { throw KC.error(s) }
        return d
    }

    func put(_ kind: ItemKind, account: String, data: Data) throws {
        let s = SecItemUpdate(KC.query(match(kind, account)), [kSecValueData: data] as CFDictionary)
        if s == errSecSuccess { return }
        guard s == errSecItemNotFound else { throw KC.error(s) }
        var add = match(kind, account)
        add[kSecValueData] = data
        for (k, v) in KC.storageAttributes { add[k] = v }
        let a = SecItemAdd(KC.query(add), nil)
        guard a == errSecSuccess else { throw KC.error(a) }
    }

    func delete(_ kind: ItemKind, account: String) throws {
        let s = SecItemDelete(KC.query(match(kind, account)))
        guard s == errSecSuccess || s == errSecItemNotFound else { throw KC.error(s) }
    }

    func list(_ kind: ItemKind, accountPrefix: String) throws -> [String] {
        var out: CFTypeRef?
        var q = match(kind, nil)
        q[kSecReturnAttributes] = true
        q[kSecMatchLimit] = kSecMatchLimitAll
        let s = SecItemCopyMatching(KC.query(q), &out)
        if s == errSecItemNotFound { return [] }
        guard s == errSecSuccess, let rows = out as? [[String: Any]] else { throw KC.error(s) }
        return rows.compactMap { $0[kSecAttrAccount as String] as? String }.filter { $0.hasPrefix(accountPrefix) }
    }
}

struct SecKeySigner: SigningKey {
    let key: SecKey

    func sign(_ message: Data) throws -> Data {
        var e: Unmanaged<CFError>?
        guard let sig = SecKeyCreateSignature(key, .rsaSignatureMessagePKCS1v15SHA256, message as CFData, &e) else {
            let code = e.map { Int32(CFErrorGetCode($0.takeRetainedValue())) } ?? -1
            throw SignerError.keychain(osstatus: code, message: "signing failed")
        }
        return sig as Data
    }
}

struct KeychainKeyStore: KeyStore {
    private func tag(_ slug: String) -> Data { Data("taaad-signer.key.\(slug)".utf8) }

    func makeKey(pkcs1DER: Data) throws -> any SigningKey {
        var e: Unmanaged<CFError>?
        guard let key = SecKeyCreateWithData(pkcs1DER as CFData, [
            kSecAttrKeyType: kSecAttrKeyTypeRSA,
            kSecAttrKeyClass: kSecAttrKeyClassPrivate,
        ] as CFDictionary, &e) else {
            _ = e?.takeRetainedValue()
            throw SignerError.badKey("the system rejected the RSA key")
        }
        return SecKeySigner(key: key)
    }

    func add(_ key: any SigningKey, slug: String) throws {
        guard let signer = key as? SecKeySigner else { throw SignerError.internalError("unexpected key type") }
        var q: [CFString: Any] = [
            kSecClass: kSecClassKey, kSecValueRef: signer.key,
            kSecAttrApplicationTag: tag(slug), kSecAttrLabel: "taaad-signer \(slug)",
            kSecAttrIsExtractable: false,
        ]
        for (k, v) in KC.storageAttributes { q[k] = v }
        let s = SecItemAdd(KC.query(q), nil)
        guard s == errSecSuccess else { throw KC.error(s) }
    }

    func find(slug: String) throws -> (any SigningKey)? {
        var out: CFTypeRef?
        let s = SecItemCopyMatching(KC.query([
            kSecClass: kSecClassKey, kSecAttrApplicationTag: tag(slug),
            kSecAttrKeyClass: kSecAttrKeyClassPrivate,
            kSecReturnRef: true, kSecMatchLimit: kSecMatchLimitAll,
        ]), &out)
        if s == errSecItemNotFound { return nil }
        guard s == errSecSuccess else { throw KC.error(s) }
        let keys: [SecKey]
        if let many = out as? [SecKey] { keys = many }
        else if let one = out, CFGetTypeID(one) == SecKeyGetTypeID() { keys = [one as! SecKey] }
        else { throw SignerError.keychain(osstatus: errSecInternalError, message: "keychain: unexpected lookup result") }
        guard keys.count <= 1 else {
            throw SignerError.keychain(osstatus: errSecDuplicateItem, message: "keychain: more than one key for slug \(slug)")
        }
        return keys.first.map { SecKeySigner(key: $0) }
    }

    func delete(slug: String) throws {
        let s = SecItemDelete(KC.query([kSecClass: kSecClassKey, kSecAttrApplicationTag: tag(slug)]))
        guard s == errSecSuccess || s == errSecItemNotFound else { throw KC.error(s) }
    }
}
