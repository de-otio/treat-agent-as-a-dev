import Foundation

public enum JWT {
    public static func base64url(_ d: Data) -> String {
        d.base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }

    public static let headerJSON = "{\"alg\":\"RS256\",\"typ\":\"JWT\"}"

    /// Compact JSON with a fixed key order.
    public static func payloadJSON(appId: Int, now: Int) -> String {
        "{\"iat\":\(now - 60),\"exp\":\(now + 120),\"iss\":\"\(appId)\"}"
    }

    public static func signingInput(appId: Int, now: Int) -> String {
        base64url(Data(headerJSON.utf8)) + "." + base64url(Data(payloadJSON(appId: appId, now: now).utf8))
    }

    public static func make(appId: Int, now: Int, key: any SigningKey) throws -> String {
        let input = signingInput(appId: appId, now: now)
        let sig = try key.sign(Data(input.utf8))
        return input + "." + base64url(sig)
    }
}
