import Foundation

public enum SignerInfo {
    public static let version = "0.1.0"
    public static let identifier = "org.de-otio.taaad-signer"
    public static let apiBase = "https://api.github.com"
    public static let userAgent = "taaad-signer/\(version)"
}

/// Every failure the helper can report. Messages never contain a token, a key or a request header.
public enum SignerError: Error, Equatable, Sendable {
    case usage(String)
    case notFound(slug: String)
    case presenceDenied(String)
    case rateLimited(retryAfter: Int)
    case github(status: Int, message: String)
    case widened
    case keychain(osstatus: Int32, message: String)
    case exists(slug: String)
    case badKey(String)
    case appMismatch(expected: Int, actual: Int)
    case appIdChanged(stored: Int, requested: Int)
    case internalError(String)

    public var code: String {
        switch self {
        case .usage: "usage"
        case .notFound: "not-found"
        case .presenceDenied: "presence-denied"
        case .rateLimited: "rate-limited"
        case .github: "github"
        case .widened: "widened"
        case .keychain: "keychain"
        case .exists: "exists"
        case .badKey: "bad-key"
        case .appMismatch, .appIdChanged: "app-mismatch"
        case .internalError: "internal"
        }
    }

    public var exitCode: Int32 {
        switch self {
        case .usage: 2
        case .notFound: 3
        case .presenceDenied: 4
        case .rateLimited: 5
        case .github: 6
        case .widened: 7
        case .keychain: 8
        case .exists: 9
        case .badKey: 10
        case .appMismatch, .appIdChanged: 11
        case .internalError: 1
        }
    }

    public var message: String {
        switch self {
        case .usage(let m): m
        case .notFound(let s): "no key for slug \(s)"
        case .presenceDenied(let m): m
        case .rateLimited(let r): "prompt budget spent; retry in \(r) seconds"
        case .github(_, let m): m
        case .widened: "GitHub returned a token broader than requested; it was discarded"
        case .keychain(_, let m): m
        case .exists(let s): "a key for slug \(s) already exists; use --replace"
        case .badKey(let m): m
        case .appMismatch(let e, let a): "GET /app returned id \(a), expected \(e)"
        case .appIdChanged(let s, let r): "the stored App ID is \(s), not \(r); rotation never changes the App ID"
        case .internalError(let m): m
        }
    }

    /// The JSON object written to stderr.
    public var json: JSON {
        var o: [String: JSON] = ["error": .string(code), "message": .string(message)]
        switch self {
        case .rateLimited(let r): o["retry_after"] = .int(r)
        case .github(let s, _): o["status"] = .int(s)
        case .keychain(let s, _): o["osstatus"] = .int(Int(s))
        default: break
        }
        return .object(o)
    }
}
