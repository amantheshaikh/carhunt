import path from "path";
import fs from "fs";
import { execSync } from "child_process";

export interface AgentsConfig {
  agentsDir: string;
  venvPython: string;
}

/**
 * Resolves the directory for agents and the python executable to use.
 * Checks environment variables first (AGENTS_DIR, AGENTS_PYTHON), then falls back
 * to standard relative virtualenv paths (.venv in agents/), and finally system python3.
 */
export function getAgentsConfig(): AgentsConfig {
  const agentsDir = process.env.AGENTS_DIR
    ? path.resolve(process.env.AGENTS_DIR)
    : path.resolve(process.cwd(), "agents");

  if (process.env.AGENTS_PYTHON) {
    return { agentsDir, venvPython: path.resolve(process.env.AGENTS_PYTHON) };
  }

  // Common virtualenv python paths relative to agentsDir or project root
  const candidateVenvs = [
    path.join(agentsDir, ".venv/bin/python3"),
    path.join(agentsDir, ".venv/bin/python"),
    path.join(agentsDir, ".venv/Scripts/python.exe"), // Windows
    path.join(process.cwd(), ".venv/bin/python3"),
    path.join(process.cwd(), ".venv/bin/python"),
  ];

  for (const candidate of candidateVenvs) {
    if (fs.existsSync(candidate)) {
      return { agentsDir, venvPython: candidate };
    }
  }

  return { agentsDir, venvPython: "python3" };
}

/**
 * Checks if the configured python executable is runnable.
 */
export function isPythonAvailable(pythonPath: string): boolean {
  if (fs.existsSync(pythonPath)) return true;
  try {
    execSync(`${pythonPath} --version`, { stdio: "ignore" });
    return true;
  } catch {
    return false;
  }
}

