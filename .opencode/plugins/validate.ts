import type { Plugin } from "@opencode-ai/plugin"

const ARTICLE_PATTERN = /^knowledge\/articles\/.*\.json$/

export const ValidatePlugin: Plugin = async ({ $, worktree }) => {
  return {
    "tool.execute.after": async (input, output) => {
      if (input.tool !== "write" && input.tool !== "edit") return

      const filePath: string | undefined =
        input.args?.filePath ?? input.args?.file_path
      if (!filePath) return

      const relative = filePath.startsWith(worktree)
        ? filePath.slice(worktree.length).replace(/^\//, "")
        : filePath

      if (!ARTICLE_PATTERN.test(relative)) return

      try {
        const result = await $`python3 hooks/validate_json.py ${filePath}`
          .cwd(worktree)
          .nothrow()

        const text = result.stderr.toString().trim()
        if (result.exitCode !== 0) {
          output.output += `\n\n[validate] 校验失败:\n${text}`
        } else {
          output.output += `\n\n[validate] 校验通过`
        }
      } catch (err) {
        output.output +=
          `\n\n[validate] 校验脚本异常: ${err instanceof Error ? err.message : String(err)}`
      }
    },
  }
}
