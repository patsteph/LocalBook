import re
from pathlib import Path


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    backend_root = repo_root / "backend"
    build_script = backend_root / "build_backend.sh"

    if not build_script.exists():
        raise SystemExit(f"build script not found: {build_script}")

    text = build_script.read_text()
    hidden_imports = re.findall(r"--hidden-import=([A-Za-z0-9_\.]+)", text)
    collect_all = set(re.findall(r"--collect-all=([A-Za-z0-9_\.]+)", text))

    # Third-party packages that MUST be pulled whole with --collect-all, else the app breaks at
    # runtime in the built .app (not in dev):
    #   - sqlglot: lazily imports its sqlite dialect inside parse_one() (SQL validation).
    #   - docx (python-docx): ships templates/*.xml DATA files (default-header.xml, …) that a
    #     .docx read/write loads; relying on the hooks-contrib hook silently dropped them → core
    #     .docx ingestion 500'd ("No such file: docx/templates/default-header.xml"). Explicit like pptx.
    #   - pptx (python-pptx): same class of templated data files.
    required_collect_all = {"sqlglot", "docx", "pptx"}
    missing_collect = sorted(required_collect_all - collect_all)
    if missing_collect:
        print("Missing --collect-all for dynamic-import packages in backend/build_backend.sh:")
        for m in missing_collect:
            print(f"- {m}  (add: --collect-all={m})")
        return 1

    local_prefixes = (
        "api",
        "services",
        "storage",
        "models",
        "utils",
        "config",
    )

    missing = []

    for mod in hidden_imports:
        if not (mod == "config" or mod.startswith(local_prefixes)):
            continue

        rel = Path(*mod.split("."))
        py_candidate = backend_root / (str(rel) + ".py")
        pkg_candidate = backend_root / rel / "__init__.py"

        if py_candidate.exists() or pkg_candidate.exists():
            continue

        missing.append(mod)

    if missing:
        print("Missing local hidden-import targets in backend/build_backend.sh:")
        for m in sorted(set(missing)):
            print(f"- {m}")
        return 1

    print("OK: All local --hidden-import entries resolve to backend modules")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
