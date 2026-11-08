import Foundation

/// A small immutable JSON value. Output is always compact with sorted keys, so it is deterministic.
public enum JSON: Equatable, Sendable {
    case null
    case bool(Bool)
    case int(Int)
    case double(Double)
    case string(String)
    case array([JSON])
    case object([String: JSON])

    public subscript(key: String) -> JSON? {
        if case .object(let o) = self { return o[key] }
        return nil
    }
    public var stringValue: String? { if case .string(let s) = self { return s } else { return nil } }
    public var intValue: Int? { if case .int(let i) = self { return i } else { return nil } }
    public var objectValue: [String: JSON]? { if case .object(let o) = self { return o } else { return nil } }
    public var arrayValue: [JSON]? { if case .array(let a) = self { return a } else { return nil } }

    /// Parses a JSON document whose top level is an object or an array. Anything else is nil.
    public static func parse(_ data: Data) -> JSON? {
        guard let any = try? JSONSerialization.jsonObject(with: data, options: []) else { return nil }
        guard any is [String: Any] || any is [Any] else { return nil }
        return from(foundation: any)
    }

    static func from(foundation v: Any) -> JSON? {
        switch v {
        case is NSNull: return .null
        case let n as NSNumber:
            if CFGetTypeID(n) == CFBooleanGetTypeID() { return .bool(n.boolValue) }
            if CFNumberIsFloatType(n) { return .double(n.doubleValue) }
            return .int(n.intValue)
        case let s as String: return .string(s)
        case let a as [Any]:
            var out: [JSON] = []
            for e in a { guard let j = from(foundation: e) else { return nil }; out.append(j) }
            return .array(out)
        case let d as [String: Any]:
            var out: [String: JSON] = [:]
            for (k, e) in d { guard let j = from(foundation: e) else { return nil }; out[k] = j }
            return .object(out)
        default: return nil
        }
    }

    func toFoundation() -> Any {
        switch self {
        case .null: return NSNull()
        case .bool(let b): return NSNumber(value: b)
        case .int(let i): return NSNumber(value: i)
        case .double(let d): return NSNumber(value: d)
        case .string(let s): return s
        case .array(let a): return a.map { $0.toFoundation() }
        case .object(let o): return o.mapValues { $0.toFoundation() }
        }
    }

    /// Compact, sorted-key, single-line serialization. Top level must be an object or array.
    public func serialized() -> String {
        let obj = toFoundation()
        guard JSONSerialization.isValidJSONObject(obj),
              let d = try? JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys, .withoutEscapingSlashes])
        else { return "{}" }
        return String(decoding: d, as: UTF8.self)
    }

    public func serializedData() -> Data { Data(serialized().utf8) }
}
