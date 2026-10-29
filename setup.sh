#!/usr/bin/env bash
# Free-tier STORM setup — first-time bootstrap.
# Usage: ./setup.sh

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

echo "=== Free-tier STORM Setup ==="
echo ""

# --- Prerequisites -----------------------------------------------------------

echo "Checking prerequisites..."

if ! command -v uv >/dev/null 2>&1; then
    echo "  uv not found. Installing it..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    echo "  uv installed."
else
    echo "  uv found."
fi

if ! command -v git >/dev/null 2>&1; then
    echo "Error: git is required to clone the upstream repository." >&2
    exit 1
fi

# --- Upstream repository -----------------------------------------------------

if [ ! -d "storm/.git" ]; then
    echo ""
    echo "Cloning stanford-oval/storm..."
    git clone --depth 1 https://github.com/stanford-oval/storm.git storm
else
    echo "  storm/ already cloned."
fi

# --- Python environment ------------------------------------------------------
#
# The upstream repository pins dspy_ai==2.4.9, which predates Python 3.12+.
# Python 3.11 is the newest release the dependency stack supports cleanly.

echo ""
echo "Setting up the Python 3.11 environment..."
uv python install 3.11
uv venv --python 3.11 venv
export VIRTUAL_ENV="$PROJECT_DIR/venv"

# The upstream requirements.txt leaves sentence-transformers, transformers and
# torch unpinned, which resolves to a combination that cannot import: the
# newest transformers expects a newer torch, and the newest torch pulls in
# ~2.5GB of CUDA libraries this machine does not need.  Pin the versions that
# are known to work together, with a CPU-only torch.
PYTORCH_CPU_INDEX="https://download.pytorch.org/whl/cpu"
export UV_HTTP_TIMEOUT=300

echo "Installing CPU-only torch and torchvision..."
uv pip install --extra-index-url "$PYTORCH_CPU_INDEX" --index-strategy unsafe-best-match \
    "torch==2.4.1" "torchvision==0.19.1"

echo "Installing the remaining requirements..."
uv pip install --extra-index-url "$PYTORCH_CPU_INDEX" --index-strategy unsafe-best-match \
    -r storm/requirements.txt

# sentence-transformers 2.2.2 (the version the unpin resolves to) imports
# huggingface_hub.cached_download, removed in newer hub releases.
echo "Pinning a compatible sentence-transformers..."
uv pip install "sentence-transformers==3.4.1" "huggingface_hub>=0.23,<1.0"

# The upstream DuckDuckGo retriever imports ``duckduckgo_search``, which has
# since been renamed to ``ddgs``.  The old package still installs, but its
# default backend now returns unrelated pages instead of search results, so
# we install ``ddgs`` and use it (see free_setup/retrievers.py).
uv pip install ddgs pytest

# The Streamlit UI is optional; skip its dependencies if the user does not
# want them.  Set INSTALL_UI=1 to include it.
if [ "${INSTALL_UI:-0}" = "1" ]; then
    echo "Installing the Streamlit UI dependencies..."
    uv pip install -r storm/frontend/demo_light/requirements.txt
    # The newest pyarrow requires NumPy 2.0+, but the upstream requirements
    # pin NumPy to 1.26.x, and the two cannot coexist.  pyarrow 16.1 is the
    # last release that works with NumPy 1.x.
    uv pip install "pyarrow==16.1.0"
fi

# --- Configuration -----------------------------------------------------------

if [ ! -f secrets.toml ]; then
    cp secrets.toml.example secrets.toml
    echo ""
    echo "Created secrets.toml from the template."
else
    echo "  secrets.toml already exists; leaving it alone."
fi

# --- Verification ------------------------------------------------------------

echo ""
echo "Verifying the installation..."
./venv/bin/python -c "import knowledge_storm; print('  knowledge_storm imports OK')" 2>&1 | sed 's/^/  /'
./venv/bin/python -c "import free_setup; print('  free_setup imports OK')" 2>&1 | sed 's/^/  /'

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  1. Add API keys to secrets.toml (any one of them is enough to start):"
echo "       nano secrets.toml"
echo "     Free, no credit card: Groq, Gemini, Mistral, OpenRouter, NVIDIA NIM."
echo ""
echo "  2. Check that your providers respond:"
echo "       ./venv/bin/python run_storm_free.py --doctor"
echo ""
echo "  3. Run it:"
echo "       ./venv/bin/python run_storm_free.py \"your topic here\""
echo ""
echo "  Optional: install the Streamlit UI with INSTALL_UI=1 ./setup.sh"
