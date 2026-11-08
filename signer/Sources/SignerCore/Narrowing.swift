import CryptoKit
import Foundation

public enum Validate {
    private static func isLower(_ c: UInt8) -> Bool { c >= 0x61 && c <= 0x7A }
    private static func isDigit(_ c: UInt8) -> Bool { c >= 0x30 && c <= 0x39 }
    private static func isUpper(_ c: UInt8) -> Bool { c >= 0x41 && c <= 0x5A }

    /// `^[a-z0-9][a-z0-9-]{0,38}$`, checked byte-wise (no regex, so no `$`-before-newline trap).
    public static func slug(_ s: String) throws -> String {
        let b = Array(s.utf8)
        let ok = (1...39).contains(b.count)
            && (isLower(b[0]) || isDigit(b[0]))
            && b.allSatisfy { isLower($0) || isDigit($0) || $0 == 0x2D }
        guard ok else { throw SignerError.usage("invalid slug (expected ^[a-z0-9][a-z0-9-]{0,38}$)") }
        return s
    }

    /// Decimal digits only, no leading zero, 1...2^53.
    public static func positiveInt(_ s: String, what: String) throws -> Int {
        let b = Array(s.utf8)
        guard !b.isEmpty, b.count <= 16, b.allSatisfy(isDigit), b[0] != 0x30,
              let n = Int(s), n >= 1, n <= 9_007_199_254_740_992
        else { throw SignerError.usage("invalid \(what) (expected a decimal integer in 1...2^53)") }
        return n
    }

    /// `^[A-Za-z0-9._-]{1,100}$`
    public static func repoName(_ s: String) throws -> String {
        let b = Array(s.utf8)
        guard (1...100).contains(b.count),
              b.allSatisfy({ isLower($0) || isUpper($0) || isDigit($0) || $0 == 0x2E || $0 == 0x5F || $0 == 0x2D })
        else { throw SignerError.usage("invalid repository name") }
        return s
    }

    /// `NAME=LEVEL`, NAME `^[a-z][a-z_]{0,63}$`, LEVEL read|write|admin.
    public static func permission(_ s: String) throws -> (name: String, level: String) {
        let parts = s.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false)
        guard parts.count == 2 else { throw SignerError.usage("invalid permission (expected NAME=LEVEL)") }
        let name = String(parts[0]), level = String(parts[1])
        let b = Array(name.utf8)
        guard (1...64).contains(b.count), isLower(b[0]), b.allSatisfy({ isLower($0) || $0 == 0x5F }) else {
            throw SignerError.usage("invalid permission name")
        }
        guard ["read", "write", "admin"].contains(level) else {
            throw SignerError.usage("invalid permission level (read, write or admin)")
        }
        return (name, level)
    }

    /// Account logins are interpolated into the presence prompt, so GitHub's answer is checked too.
    public static func isPlausibleLogin(_ s: String) -> Bool {
        let b = Array(s.utf8)
        return (1...100).contains(b.count)
            && (isLower(b[0]) || isUpper(b[0]) || isDigit(b[0]))
            && b.allSatisfy { isLower($0) || isUpper($0) || isDigit($0) || $0 == 0x2D || $0 == 0x5F || $0 == 0x2E }
    }
}

public enum PermissionLevel {
    public static func rank(_ level: String) -> Int? {
        switch level {
        case "read": 1
        case "write": 2
        case "admin": 3
        default: nil
        }
    }
}

/// What the caller asked the token to be limited to. Immutable and validated at construction.
public struct Narrowing: Equatable, Sendable {
    /// Deduplicated case-insensitively, first spelling and order kept (sent to GitHub as given).
    public let repositories: [String]
    public let permissions: [String: String]

    public init(repositories: [String], permissions: [(name: String, level: String)]) throws {
        var seen = Set<String>()
        var repos: [String] = []
        for r in repositories {
            _ = try Validate.repoName(r)
            if seen.insert(r.lowercased()).inserted { repos.append(r) }
        }
        guard repos.count <= 100 else { throw SignerError.usage("more than 100 repositories") }
        var perms: [String: String] = [:]
        for p in permissions {
            guard perms[p.name] == nil else { throw SignerError.usage("permission \(p.name) given twice") }
            perms[p.name] = p.level
        }
        self.repositories = repos
        self.permissions = perms
    }

    public static let none = try! Narrowing(repositories: [], permissions: [])

    public var isEmpty: Bool { repositories.isEmpty && permissions.isEmpty }

    /// Lowercased, sorted, unique.
    public var canonicalRepositories: [String] {
        Array(Set(repositories.map { $0.lowercased() })).sorted(by: Narrowing.byteOrder)
    }

    public var sortedPermissions: [(name: String, level: String)] {
        permissions.keys.sorted(by: Narrowing.byteOrder).map { ($0, permissions[$0]!) }
    }

    static func byteOrder(_ a: String, _ b: String) -> Bool { a.utf8.lexicographicallyPrecedes(b.utf8) }

    /// `{"permissions":{<sorted by name>},"repositories":[<lowercased, sorted, unique>]}`, no spaces.
    /// Every string is validated to need no escaping.
    public var canonicalJSON: String {
        let p = sortedPermissions.map { "\"\($0.name)\":\"\($0.level)\"" }.joined(separator: ",")
        let r = canonicalRepositories.map { "\"\($0)\"" }.joined(separator: ",")
        return "{\"permissions\":{\(p)},\"repositories\":[\(r)]}"
    }

    /// Lowercase hex SHA-256 of `canonicalJSON`.
    public var hash: String {
        SHA256.hash(data: Data(canonicalJSON.utf8)).map { String(format: "%02x", $0) }.joined()
    }

    /// The POST body: only what was requested; empty when nothing was.
    public var requestBody: Data {
        var o: [String: JSON] = [:]
        if !repositories.isEmpty { o["repositories"] = .array(repositories.map { .string($0) }) }
        if !permissions.isEmpty { o["permissions"] = .object(permissions.mapValues { .string($0) }) }
        return o.isEmpty ? Data() : JSON.object(o).serializedData()
    }
}

public struct TokenGrant: Equatable, Sendable {
    public let token: String
    public let expiresAt: String
    public let permissions: [String: String]
    public let repositorySelection: String
    public let repositories: [String]

    public init(token: String, expiresAt: String, permissions: [String: String], repositorySelection: String, repositories: [String]) {
        self.token = token; self.expiresAt = expiresAt; self.permissions = permissions
        self.repositorySelection = repositorySelection; self.repositories = repositories
    }

    /// nil when the response lacks a token or a parseable expiry.
    public static func parse(_ json: JSON) -> TokenGrant? {
        guard let token = json["token"]?.stringValue, !token.isEmpty,
              let exp = json["expires_at"]?.stringValue, Time.parseISO8601(exp) != nil
        else { return nil }
        var perms: [String: String] = [:]
        if let p = json["permissions"]?.objectValue {
            // A non-string level is kept as "" so the widening check rejects it.
            for (k, v) in p { perms[k] = v.stringValue ?? "" }
        } else if json["permissions"] != nil, json["permissions"] != .null {
            perms["?"] = ""
        }
        var repos: [String] = []
        if let a = json["repositories"]?.arrayValue {
            for e in a { repos.append(e["name"]?.stringValue ?? "") }
        } else if json["repositories"] != nil, json["repositories"] != .null {
            repos.append("")
        }
        return TokenGrant(token: token, expiresAt: exp, permissions: perms,
                          repositorySelection: json["repository_selection"]?.stringValue ?? "", repositories: repos)
    }
}

public enum Widening {
    /// True when `granted` is broader than `requested`. Pure.
    public static func violates(requested: Narrowing, granted: TokenGrant) -> Bool {
        if !requested.permissions.isEmpty {
            for (name, level) in granted.permissions {
                // GitHub adds `metadata: read` to every narrowed token; anything above read still counts.
                if name == "metadata", level == "read" { continue }
                guard let want = requested.permissions[name],
                      let wantRank = PermissionLevel.rank(want),
                      let gotRank = PermissionLevel.rank(level),
                      gotRank <= wantRank
                else { return true }
            }
        }
        if !requested.repositories.isEmpty {
            guard granted.repositorySelection == "selected" else { return true }
            let allowed = Set(requested.repositories.map { $0.lowercased() })
            for name in granted.repositories where !allowed.contains(name.lowercased()) { return true }
        }
        return false
    }
}

public enum Time {
    public static func parseISO8601(_ s: String) -> Date? {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        if let d = f.date(from: s) { return d }
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f.date(from: s)
    }

    public static func formatISO8601(_ d: Date) -> String {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f.string(from: d)
    }
}
