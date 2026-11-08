import Foundation

/// Presence-prompt text. macOS renders `"taaad-signer" <wants to> <reason>`, so these are verb phrases.
public enum Reasons {
    static func limited(_ items: [String]) -> String {
        items.count > 5 ? items.prefix(5).joined(separator: ", ") + " and \(items.count - 5) more" : items.joined(separator: ", ")
    }

    public static func importKey(slug: String, appId: Int) -> String {
        "import the GitHub App private key for \(slug) (App ID \(appId))"
    }

    public static func replaceKey(slug: String, appId: Int) -> String {
        "replace the GitHub App private key for \(slug) (App ID \(appId))"
    }

    public static func deleteKey(slug: String) -> String {
        "delete the GitHub App private key for \(slug)"
    }

    public static func mint(slug: String, account: String, narrowing: Narrowing) -> String {
        let r = narrowing.repositories.isEmpty ? "all the App can reach" : limited(narrowing.canonicalRepositories)
        let p = narrowing.permissions.isEmpty
            ? "all the App was granted"
            : limited(narrowing.sortedPermissions.map { "\($0.name):\($0.level)" })
        return "mint a GitHub token for \(slug) on \(account) (repositories: \(r); permissions: \(p))"
    }
}
