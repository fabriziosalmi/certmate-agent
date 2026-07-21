// The commands this agent offers. It answers from the CertMate
// documentation and has no connection to a running instance, so there is one
// list — the mode split (and a `SLASH_COMMANDS_FULL` export that was imported
// but never defined, which threw a SyntaxError and stopped the whole web
// component from registering) is gone.
export const SLASH_COMMANDS = [
  ["/help", "List all commands"],
  ["/docs", "Search CertMate docs (RAG) (/docs DNS-01)"],
];
