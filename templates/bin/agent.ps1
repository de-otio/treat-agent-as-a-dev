# usage: .\bin\agent.ps1 claude     # Claude Code
#        .\bin\agent.ps1 codex      # OpenAI Codex CLI
#
# Sources the env wrapper (mints a fresh installation token, exports
# the bot git identity), then runs the agent CLI of your choice.
. "$PSScriptRoot\agent-env.ps1"
& $args[0] @($args[1..($args.Length - 1)])
