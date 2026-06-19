// Generate the dashboard's TypeScript types from the live FastAPI OpenAPI schema.
// The Pydantic models are the single contract source: a breaking change here fails `tsc`.
import { execSync } from "node:child_process"
import { mkdirSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

const scriptDir = path.dirname(fileURLToPath(import.meta.url))
const dashboardDir = path.resolve(scriptDir, "..")
const repoRoot = path.resolve(dashboardDir, "..")
const schemaPath = path.join(dashboardDir, "openapi.json")
const outPath = path.join(dashboardDir, "src", "lib", "api.gen.ts")

console.log("· dumping OpenAPI schema from the FastAPI app")
execSync(`uv run python -m pythia.api.openapi > "${schemaPath}"`, {
  cwd: repoRoot,
  stdio: ["ignore", "ignore", "inherit"],
})

console.log("· generating TypeScript types")
mkdirSync(path.dirname(outPath), { recursive: true })
execSync(`npx --no-install openapi-typescript "${schemaPath}" -o "${outPath}"`, {
  cwd: dashboardDir,
  stdio: "inherit",
})

console.log(`✓ ${path.relative(dashboardDir, outPath)}`)
