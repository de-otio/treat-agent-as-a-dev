import Foundation

public enum Command: Equatable, Sendable {
    case version
    case status(slug: String)
    case importKey(slug: String, appId: Int, replace: Bool)
    case mint(slug: String, installId: Int, narrowing: Narrowing)
    case appMeta(slug: String)
    case installations(slug: String)
    case delete(slug: String)
}

public enum Arguments {
    /// `args` excludes argv[0].
    public static func parse(_ args: [String]) throws -> Command {
        guard let name = args.first else { throw SignerError.usage("missing command") }
        let rest = Array(args.dropFirst())
        switch name {
        case "version":
            guard rest.isEmpty else { throw SignerError.usage("version takes no arguments") }
            return .version
        case "status", "app-meta", "installations", "delete":
            guard rest.count == 1 else { throw SignerError.usage("\(name) takes exactly one argument: <slug>") }
            let slug = try Validate.slug(rest[0])
            switch name {
            case "status": return .status(slug: slug)
            case "app-meta": return .appMeta(slug: slug)
            case "installations": return .installations(slug: slug)
            default: return .delete(slug: slug)
            }
        case "import":
            let p = try split(rest, valueOptions: [], flags: ["--replace"])
            guard p.positionals.count == 2 else { throw SignerError.usage("usage: import <slug> <app_id> [--replace]") }
            return .importKey(slug: try Validate.slug(p.positionals[0]),
                              appId: try Validate.positiveInt(p.positionals[1], what: "app_id"),
                              replace: p.flags.contains("--replace"))
        case "mint":
            let p = try split(rest, valueOptions: ["--repo", "--perm"], flags: [])
            guard p.positionals.count == 2 else {
                throw SignerError.usage("usage: mint <slug> <install_id> [--repo NAME]... [--perm NAME=LEVEL]...")
            }
            let narrowing = try Narrowing(
                repositories: p.values.filter { $0.0 == "--repo" }.map { $0.1 },
                permissions: try p.values.filter { $0.0 == "--perm" }.map { try Validate.permission($0.1) })
            return .mint(slug: try Validate.slug(p.positionals[0]),
                         installId: try Validate.positiveInt(p.positionals[1], what: "install_id"),
                         narrowing: narrowing)
        default:
            throw SignerError.usage("unknown command")
        }
    }

    private struct Split {
        var positionals: [String] = []
        var values: [(String, String)] = []
        var flags: Set<String> = []
    }

    private static func split(_ args: [String], valueOptions: Set<String>, flags: Set<String>) throws -> Split {
        var out = Split()
        var i = 0
        while i < args.count {
            let a = args[i]
            if valueOptions.contains(a) {
                guard i + 1 < args.count, !args[i + 1].hasPrefix("--") else { throw SignerError.usage("\(a) needs a value") }
                out.values.append((a, args[i + 1]))
                i += 2
            } else if flags.contains(a) {
                guard out.flags.insert(a).inserted else { throw SignerError.usage("\(a) given twice") }
                i += 1
            } else if a.hasPrefix("-") {
                throw SignerError.usage("unknown option")
            } else {
                out.positionals.append(a)
                i += 1
            }
        }
        return out
    }
}
