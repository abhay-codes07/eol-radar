"""Surface 4 of 5: managed runtimes declared for deployment.

Serverless Framework, SAM/CloudFormation, Terraform, CDK, App Engine, Vercel
and Netlify all name the runtime their platform will execute. Those names have
their own retirement clocks, separate from the language's own end of life.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common as c
import eol_data as ed

SURFACE = "cloud"

_TF_RUNTIME = re.compile(r'^\s*runtime\s*=\s*["\']([^"\']+)["\']', re.M)
_CDK_RUNTIME = re.compile(r"\bRuntime\.([A-Z][A-Z0-9_]*)\b")
_NETLIFY_NODE = re.compile(r'^\s*NODE_VERSION\s*=\s*["\']?([0-9][0-9.]*)', re.M)

LAMBDA_PREFIXES = ("nodejs", "python", "java", "dotnet", "ruby", "go", "provided")


def _is_template(base, _path):
    lowered = base.lower()
    if lowered in ("template.yml", "template.yaml", "serverless.yml", "serverless.yaml"):
        return True
    return lowered.endswith((".template.yml", ".template.yaml", ".cfn.yml", ".cfn.yaml"))


def _is_terraform(base, _path):
    return base.lower().endswith(".tf")


def _is_cdk_source(base, _path):
    lowered = base.lower()
    if lowered.endswith((".d.ts", ".min.js", ".test.ts", ".spec.ts")):
        return False
    return lowered.endswith((".ts", ".js", ".py", ".java", ".go", ".cs"))


def _looks_like_lambda_runtime(value):
    lowered = (value or "").strip().lower()
    return lowered.startswith(LAMBDA_PREFIXES) and any(ch.isdigit() for ch in lowered) or lowered.startswith("provided")


def _cdk_constant_to_runtime(constant):
    """NODEJS_20_X -> nodejs20.x, PYTHON_3_9 -> python3.9, GO_1_X -> go1.x."""
    parts = constant.split("_")
    if not parts:
        return None
    language = parts[0].lower()
    rest = parts[1:]
    if language == "dotnet" and rest and rest[0].upper() == "CORE":
        language, rest = "dotnetcore", rest[1:]
    if not rest:
        return None
    numbers = []
    suffix = ""
    for token in rest:
        if token.isdigit():
            numbers.append(token)
        elif token.upper() == "X":
            suffix = ".x"
        else:
            suffix = "." + token.lower()
    version = ".".join(numbers)
    if not version and not suffix:
        return None
    return language + version + suffix


def _add_lambda(subjects, runtime, where, raw, platform="aws-lambda"):
    runtime = runtime.strip().lower()
    subjects.append(c.subject(
        "cloud-runtime", "AWS Lambda " + runtime, where, raw,
        c.eol_lookup("aws-lambda", runtime),
        fix="migrate the function to a supported Lambda runtime",
        extra={"platform": platform, "cycle": runtime},
    ))


def _yaml_runtimes(root, path, subjects):
    text = c.read_text(path)
    where_file = c.rel(root, path)
    for number, _indent, key, value in c.yaml_pairs(text):
        if key.lower() != "runtime":
            continue
        raw = c.scalar(value)
        if not raw or "${" in raw:
            continue
        where = where_file + ":" + str(number)
        lowered = raw.lower()
        if _looks_like_lambda_runtime(lowered):
            _add_lambda(subjects, lowered, where, "runtime: " + raw)
        elif re.match(r"^(python|nodejs|java|php|ruby|go)\d+$", lowered):
            # App Engine style: python39, nodejs20
            match = re.match(r"^([a-z]+)(\d+)$", lowered)
            language, digits = match.group(1), match.group(2)
            product = {"python": "python", "nodejs": "nodejs", "java": "eclipse-temurin",
                       "php": "php", "ruby": "ruby", "go": "go"}.get(language)
            version = digits if len(digits) <= 2 and language != "python" else (
                digits[0] + "." + digits[1:] if len(digits) > 1 else digits)
            if product:
                subjects.append(c.subject(
                    "cloud-runtime", "App Engine " + raw, where, "runtime: " + raw,
                    c.eol_lookup(product, version),
                    extra={"platform": "app-engine", "cycle": version},
                ))


def _terraform(root, path, subjects):
    text = c.read_text(path)
    where_file = c.rel(root, path)
    for match in _TF_RUNTIME.finditer(text):
        value = match.group(1)
        if not _looks_like_lambda_runtime(value):
            continue
        line = text[:match.start()].count("\n") + 1
        _add_lambda(subjects, value, where_file + ":" + str(line), match.group(0).strip())


def _cdk(root, path, subjects):
    text = c.read_text(path)
    if "Runtime." not in text:
        return
    where_file = c.rel(root, path)
    seen = set()
    for match in _CDK_RUNTIME.finditer(text):
        runtime = _cdk_constant_to_runtime(match.group(1))
        if not runtime or not _looks_like_lambda_runtime(runtime):
            continue
        line = text[:match.start()].count("\n") + 1
        if (runtime, line) in seen:
            continue
        seen.add((runtime, line))
        _add_lambda(subjects, runtime, where_file + ":" + str(line), match.group(0))


def _vercel(root, path, subjects):
    """Vercel takes its Node version from engines.node or .nvmrc."""
    where = c.rel(root, path) + ":1"
    version = None
    source = None
    package_json = c.read_json(os.path.join(root, "package.json"))
    if isinstance(package_json, dict):
        engines = package_json.get("engines")
        if isinstance(engines, dict) and engines.get("node"):
            version = ed.clean_version(engines.get("node"))
            source = "package.json engines.node = " + str(engines.get("node"))
    if not version:
        nvmrc = os.path.join(root, ".nvmrc")
        if os.path.isfile(nvmrc):
            version = ed.clean_version(c.read_text(nvmrc))
            source = ".nvmrc"
    if not version:
        subjects.append(c.subject(
            "cloud-runtime", "Vercel project", where, "vercel.json",
            c.no_lookup("no Node version declared in the repository"),
            note="Vercel uses the version set in project settings; not visible from the repo",
            extra={"platform": "vercel"},
        ))
        return
    major = version.split(".")[0]
    subjects.append(c.subject(
        "cloud-runtime", "Vercel Node " + major, where, source or "vercel.json",
        c.eol_lookup("nodejs", major),
        fix="raise the Node version in engines/.nvmrc and project settings",
        extra={"platform": "vercel", "cycle": major},
    ))


def _netlify(root, path, subjects):
    text = c.read_text(path)
    match = _NETLIFY_NODE.search(text)
    if not match:
        return
    version = ed.clean_version(match.group(1))
    if not version:
        return
    line = text[:match.start()].count("\n") + 1
    major = version.split(".")[0]
    subjects.append(c.subject(
        "cloud-runtime", "Netlify Node " + major, c.rel(root, path) + ":" + str(line),
        match.group(0).strip(), c.eol_lookup("nodejs", major),
        extra={"platform": "netlify", "cycle": major},
    ))


def scan(root, max_depth=8):
    subjects = []
    templates = c.find_files(root, _is_template, max_depth)
    for path in templates:
        _yaml_runtimes(root, path, subjects)

    appyaml = c.find_files(root, c.name_in("app.yaml", "app.yml"), max_depth)
    for path in appyaml:
        _yaml_runtimes(root, path, subjects)

    terraform = c.find_files(root, _is_terraform, max_depth)
    for path in terraform:
        _terraform(root, path, subjects)

    cdk_files = []
    if os.path.isfile(os.path.join(root, "cdk.json")) or any(
            "cdk" in c.rel(root, p).lower() for p in templates):
        cdk_files = c.find_files(root, _is_cdk_source, min(max_depth, 6))
        for path in cdk_files:
            _cdk(root, path, subjects)

    vercel = c.find_files(root, c.name_in("vercel.json"), max_depth)
    for path in vercel:
        _vercel(root, path, subjects)

    netlify = c.find_files(root, c.name_in("netlify.toml"), max_depth)
    for path in netlify:
        _netlify(root, path, subjects)

    scanned = len(templates) + len(appyaml) + len(terraform) + len(cdk_files) + len(vercel) + len(netlify)
    return subjects, scanned


def main(argv):
    root = c.check_root(c.arg_value(argv, "--root", "."))
    depth = int(c.arg_value(argv, "--depth", "8"))
    subjects, scanned = scan(root, depth)
    warning = None if scanned else "no deployment manifests found (serverless, SAM, Terraform, CDK, Vercel, Netlify)"
    c.emit(c.ok(SURFACE, subjects, warning, scanned))


if __name__ == "__main__":
    main(sys.argv[1:])
