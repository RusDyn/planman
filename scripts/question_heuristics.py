"""File-based heuristic question analysis.

Scans project indicator files to answer obvious questions.
Zero external dependencies, <100ms execution.
"""

import os
import re

# Indicator files checked (deterministic order, no recursion into dirs)
INDICATOR_FILES = [
    "package.json",
    "tsconfig.json",
    "jsconfig.json",
    "requirements.txt",
    "pyproject.toml",
    "Cargo.toml",
    "go.mod",
    "Gemfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    ".env.example",
    "Makefile",
    "CMakeLists.txt",
    "setup.py",
]

# Directories to SKIP when counting file extensions
SKIP_DIRS = {
    "node_modules", ".git", ".hg", "__pycache__", ".venv", "venv",
    "vendor", "dist", "build", ".next", ".nuxt", "target",
    ".tox", ".mypy_cache", ".pytest_cache", "coverage",
}

# Max files to scan for extension dominance
MAX_EXTENSION_SCAN = 5000

# filename → set of tech tokens it signals
SIGNAL_MAP = {
    "tsconfig.json":        {"typescript"},
    "jsconfig.json":        {"javascript"},
    "package.json":         {"node", "javascript"},
    "requirements.txt":     {"python"},
    "pyproject.toml":       {"python"},
    "setup.py":             {"python"},
    "Cargo.toml":           {"rust"},
    "go.mod":               {"go"},
    "Gemfile":              {"ruby"},
    "CMakeLists.txt":       {"cpp"},
    "Makefile":             {"make"},
}

# Service indicators in docker-compose (parsed from service names)
COMPOSE_SERVICE_MAP = {
    "postgres":   {"postgresql"},
    "pg":         {"postgresql"},
    "mysql":      {"mysql"},
    "mongo":      {"mongodb"},
    "redis":      {"redis"},
    "rabbitmq":   {"rabbitmq"},
}

# Tech token → option label aliases (case-insensitive matching)
TECH_ALIASES = {
    "typescript":  ["typescript", "ts"],
    "javascript":  ["javascript", "js"],
    "python":      ["python", "py"],
    "postgresql":  ["postgresql", "postgres", "pg"],
    "mysql":       ["mysql"],
    "mongodb":     ["mongodb", "mongo"],
    "redis":       ["redis"],
    "rust":        ["rust"],
    "go":          ["go", "golang"],
    "ruby":        ["ruby"],
    "cpp":         ["c++", "cpp"],
    "node":        ["node", "nodejs", "node.js"],
    "make":        ["make", "makefile"],
}


def parse_compose_services(filepath):
    """Extract service names from docker-compose.yml via line-based parsing.

    Scans for top-level 'services:' key, then collects indented keys
    at the detected indent level until next top-level key.

    Returns list of service name strings, or [] on any parse error.
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except (OSError, UnicodeDecodeError):
        return []

    in_services = False
    service_indent = None
    services = []

    for line in lines:
        stripped = line.rstrip()
        if not stripped or stripped.lstrip().startswith("#"):
            continue

        # Check for top-level keys (no leading whitespace)
        if re.match(r"^\S", stripped):
            if re.match(r"^services:\s*(#.*)?$", stripped):
                in_services = True
                service_indent = None
                continue
            elif in_services:
                # New top-level key → stop
                break
            continue

        if not in_services:
            continue

        # Lines inside services block — detect indent level first
        # Service_indent is set from the first indented line (any content)
        indent_match = re.match(r"^( +)\S", stripped)
        if indent_match and service_indent is None:
            service_indent = indent_match.group(1)

        # Only capture unquoted keys at the service indent level
        # Note: `:` without trailing \s to match `postgres:` (no value after colon)
        match = re.match(r"^( +)([\w][\w.-]*):", stripped)
        if match:
            indent = match.group(1)
            name = match.group(2)
            if indent == service_indent:
                services.append(name)

    return services


def detect_tech(cwd):
    """Detect technologies from project root.

    Strategy 1: Check SIGNAL_MAP files for existence.
    Strategy 2: If docker-compose exists, parse service names.

    Returns: dict[str, list[str]] mapping tech_token → evidence list.
    """
    detected = {}

    # Strategy 1: Signal map file existence
    for filename, tokens in SIGNAL_MAP.items():
        path = os.path.join(cwd, filename)
        if os.path.isfile(path):
            for token in tokens:
                evidence = detected.get(token, [])
                evidence.append(f"{filename} exists")
                detected[token] = evidence

    # Upgrade javascript → typescript if tsconfig exists
    if "typescript" in detected and "javascript" in detected:
        # typescript is more specific, keep both but typescript takes priority in matching
        pass

    # Strategy 2: Docker-compose service detection
    for compose_name in ("docker-compose.yml", "docker-compose.yaml"):
        compose_path = os.path.join(cwd, compose_name)
        if os.path.isfile(compose_path):
            services = parse_compose_services(compose_path)
            for svc in services:
                svc_lower = svc.lower()
                for svc_pattern, tokens in COMPOSE_SERVICE_MAP.items():
                    if svc_lower == svc_pattern or svc_lower.startswith(svc_pattern):
                        for token in tokens:
                            evidence = detected.get(token, [])
                            evidence.append(f"{compose_name} service: {svc}")
                            detected[token] = evidence

    return detected


def count_extensions(cwd, extensions):
    """Count files by extension, skipping SKIP_DIRS.

    Walks at most MAX_EXTENSION_SCAN files.
    Returns dict like {".ts": 142, ".js": 8}.
    """
    counts = {ext: 0 for ext in extensions}
    total_scanned = 0

    try:
        for root, dirs, files in os.walk(cwd):
            # Skip unwanted directories (modify in-place to prevent descent)
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]

            for fname in files:
                if total_scanned >= MAX_EXTENSION_SCAN:
                    return counts
                total_scanned += 1
                _, ext = os.path.splitext(fname)
                ext_lower = ext.lower()
                if ext_lower in counts:
                    counts[ext_lower] += 1
    except OSError:
        pass

    return counts


def match_options(detected_tech, options):
    """Match option labels against detected tech via TECH_ALIASES.

    Returns (option_label, evidence_list) if EXACTLY ONE option matches.
    Returns None if zero or multiple options match (ambiguous → defer).
    """
    matches = []

    for opt in options:
        label = opt.get("label", "")
        label_lower = label.lower().strip()

        for tech_token, aliases in TECH_ALIASES.items():
            if tech_token not in detected_tech:
                continue
            for alias in aliases:
                if alias == label_lower or alias in label_lower:
                    matches.append((label, detected_tech[tech_token]))
                    break
            else:
                continue
            break  # Found a match for this option, move to next

    if len(matches) == 1:
        return matches[0]
    return None


def analyze_question(question_text, options, cwd):
    """Orchestrator. Returns {"answer": str, "evidence": [str]} or None.

    None = defer to user.
    """
    # Input validation
    if not isinstance(options, list) or len(options) < 2:
        return None
    for opt in options:
        if not isinstance(opt, dict) or "label" not in opt:
            return None
        if not isinstance(opt["label"], str):
            return None
    if not cwd or not os.path.isdir(cwd):
        return None

    # Primary: detect tech from signal map + compose
    detected = detect_tech(cwd)

    if detected:
        result = match_options(detected, options)
        if result:
            return {"answer": result[0], "evidence": result[1]}

    # Tertiary: extension dominance (>80% threshold)
    ext_map = {
        ".ts": "typescript", ".tsx": "typescript",
        ".js": "javascript", ".jsx": "javascript",
        ".py": "python",
        ".rs": "rust",
        ".go": "go",
        ".rb": "ruby",
        ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp",
    }

    extensions = list(ext_map.keys())
    counts = count_extensions(cwd, extensions)
    total = sum(counts.values())

    if total > 0:
        # Group by tech token
        tech_counts = {}
        for ext, count in counts.items():
            if count > 0:
                token = ext_map[ext]
                tech_counts[token] = tech_counts.get(token, 0) + count

        # Check for >80% dominance
        for token, count in tech_counts.items():
            ratio = count / total
            if ratio > 0.80:
                ext_detected = {token: [f"{ratio:.0%} of source files"]}
                # Check for conflict with primary detection
                if detected and token not in detected:
                    # Primary and tertiary disagree → defer
                    return None
                result = match_options(ext_detected, options)
                if result:
                    return {"answer": result[0], "evidence": result[1]}

    return None
