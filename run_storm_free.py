#!/usr/bin/env python3
"""Command-line interface for running STORM with free-tier LLM providers.

Examples::

    # See which providers are configured and whether they actually answer
    python run_storm_free.py --doctor

    # Show the registry and known free models
    python run_storm_free.py --list-providers

    # Resolve the plan without spending any tokens
    python run_storm_free.py "history of the printing press" --dry-run

    # Run the full pipeline
    python run_storm_free.py "history of the printing press"

    # Run only the research stage and reuse it later
    python run_storm_free.py "history of the printing press" \
        --do-research --do-generate-outline
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent
STORM_DIR = PROJECT_ROOT / "storm"

# The upstream package lives in ./storm; make it importable without an
# editable install so a fresh clone works immediately.
if STORM_DIR.is_dir():
    sys.path.insert(0, str(STORM_DIR))

from free_setup import (
    RETRIEVERS,
    STAGES,
    ConfigError,
    UpstreamMissingError,
    describe,
    discover_available,
    list_providers,
    load_config,
    load_secrets,
    require_upstream,
)
from free_setup.doctor import render_results, run_doctor
from free_setup.progress import CLICallbackHandler
from free_setup.providers import FREE_MODELS
from free_setup.run_log import (
    RunLog,
    attach_log_capture,
    build_run_log,
    detach_log_capture,
)
from free_setup.topic import InvalidTopicError, validate_topic

logger = logging.getLogger("storm_free")


def _load_secrets(path: Path) -> None:
    """Load API keys from a TOML file into the environment."""
    load_secrets(path)


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        prog="run_storm_free.py",
        description=(
            "Run STORM's Wikipedia-style article generation using free-tier "
            "LLM APIs, with automatic failover between providers."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Any stage flag left out reuses the cached result from a previous "
            "run, so you can research once and write later."
        ),
    )
    parser.add_argument(
        "topic", nargs="?", help="The topic to research and write about."
    )
    parser.add_argument(
        "--config",
        default=str(PROJECT_ROOT / "free_config.toml"),
        help="Path to the TOML configuration file (default: free_config.toml).",
    )
    parser.add_argument(
        "--secrets",
        default=str(PROJECT_ROOT / "secrets.toml"),
        help="Path to a TOML file with API keys (default: secrets.toml).",
    )
    parser.add_argument(
        "--output-dir",
        default=str(PROJECT_ROOT / "outputs"),
        help="Directory for generated articles (default: ./outputs).",
    )
    parser.add_argument(
        "--provider",
        action="append",
        dest="providers",
        help=(
            "Restrict the fallback chain to this provider. Repeatable. "
            "Use --list-providers to see the names."
        ),
    )
    parser.add_argument(
        "--retriever",
        default=None,
        choices=sorted(RETRIEVERS),
        help="Search backend (default: from config, normally duckduckgo).",
    )
    parser.add_argument(
        "--model",
        default=None,
        help=(
            "Override the primary model for every stage, e.g. "
            "'groq/qwen/qwen3.8-27b'. The configured fallback chain is kept."
        ),
    )
    parser.add_argument(
        "--max-conv-turn", type=int, default=None, help="Questions per conversation."
    )
    parser.add_argument(
        "--max-perspective", type=int, default=None, help="Viewpoints to explore."
    )
    parser.add_argument(
        "--search-top-k", type=int, default=None, help="Search results per query."
    )
    parser.add_argument(
        "--retrieve-top-k", type=int, default=None, help="References per section."
    )
    parser.add_argument(
        "--max-thread-num", type=int, default=None, help="Concurrent requests."
    )
    parser.add_argument(
        "--do-research", action="store_true", help="Run the research stage."
    )
    parser.add_argument(
        "--do-generate-outline", action="store_true", help="Run the outline stage."
    )
    parser.add_argument(
        "--do-generate-article", action="store_true", help="Run the article stage."
    )
    parser.add_argument(
        "--do-polish-article", action="store_true", help="Run the polish stage."
    )
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="Probe every provider and the retriever, then exit.",
    )
    parser.add_argument(
        "--list-providers",
        action="store_true",
        help="Show all known providers and their free models, then exit.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve and print the plan without making any API calls.",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Print detailed progress."
    )
    parser.add_argument(
        "--use-cache",
        action="store_true",
        help="Enable response caching (disabled by default for fallback safety).",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help=(
            "Write a per-run log to this path. "
            "Defaults to logs/runs/<date>-<topic>.log."
        ),
    )
    parser.add_argument(
        "--no-log",
        action="store_true",
        help="Do not write a per-run log.",
    )
    return parser


def cmd_list_providers() -> int:
    """Print the provider registry and known free models."""
    print("Available providers (free tier):\n")
    for spec in list_providers():
        marker = "available" if spec.is_available() else "no key "
        print(f"  {spec.name:<14} [{marker}] {spec.free_tier}")
        if spec.signup_url:
            print(f"  {'':<14}  signup: {spec.signup_url}")
        models = FREE_MODELS.get(spec.name, ())
        if models:
            print(f"  {'':<14}  models: {', '.join(models[:4])}")
        if spec.notes:
            print(f"  {'':<14}  note:   {spec.notes}")
        print()
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """Probe providers and the retriever."""
    specs = discover_available(args.providers) if args.providers else None
    if specs is None:
        specs = list_providers()
    print("Probing providers...\n")
    results = run_doctor(specs, retriever=args.retriever or "duckduckgo")
    print(render_results(results))
    usable = [r for r in results if r.usable]
    print(f"\n{len(usable)} of {len(results)} probe(s) succeeded.")
    if not usable:
        print(
            "\nNo provider responded. Add an API key to secrets.toml "
            "(see secrets.toml.example). Groq, Mistral and NVIDIA NIM are "
            "free and need no credit card."
        )
        return 1
    return 0


def _overrides(args: argparse.Namespace) -> dict[str, object]:
    """Collect the hyperparameter overrides passed on the command line."""
    mapping = {
        "max_conv_turn": args.max_conv_turn,
        "max_perspective": args.max_perspective,
        "search_top_k": args.search_top_k,
        "retrieve_top_k": args.retrieve_top_k,
        "max_thread_num": args.max_thread_num,
    }
    return {k: v for k, v in mapping.items() if v is not None}


def _quota_hint(exc: BaseException) -> str:
    """Explain what to do when a run fails because a quota ran out.

    The single most common way a free-tier run fails is not a bug: every
    provider's daily allowance is spent.  Saying so plainly is more useful
    than dumping a stack trace.
    """
    from free_setup.fallback import _is_daily_quota_error, _is_rate_limit_error

    if _is_daily_quota_error(exc):
        return (
            "Every provider in the chain has used up its daily request "
            "allowance.\n"
            "  A full STORM run makes roughly 60-80 model calls; a single "
            "free key allows about 20 per day.\n"
            "  Add another provider's key to secrets.toml (Groq, Mistral "
            "and NVIDIA NIM are free and need no card), or\n"
            "  run one stage at a time and wait for the quota to reset "
            "(midnight Pacific time for Gemini)."
        )
    if _is_rate_limit_error(exc):
        return (
            "A provider is rate limiting the run. Lower --max-thread-num, or "
            "add another provider to the chain."
        )
    return ""


def cmd_run(args: argparse.Namespace) -> int:
    """Run the STORM pipeline."""
    if not args.topic:
        print(
            "error: a topic is required (or use --doctor / --list-providers)",
            file=sys.stderr,
        )
        return 2

    try:
        validate_topic(args.topic)
    except InvalidTopicError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.model:
        from free_setup.config import StageConfig

        # ``--model`` overrides the *primary* model for every stage.  The
        # configured fallbacks are kept, because dropping them would
        # silently disable the failover that is the whole point of this
        # project -- and a user reaching for ``--model`` is often doing so
        # precisely because a provider is misbehaving.
        stages = {}
        for name in STAGES:
            original = config.stages[name]
            fallbacks = tuple(
                model for model in original.fallbacks if model != args.model
            )
            stages[name] = StageConfig(
                name=name,
                model=args.model,
                fallbacks=fallbacks,
            )
        config = type(config)(
            stages=stages,
            defaults=dict(config.defaults),
            provider_models=dict(config.provider_models),
            source_path=config.source_path,
        )

    _load_secrets(Path(args.secrets))

    try:
        # Imported here rather than at the top of the module: they pull in
        # the upstream STORM package, which the clone is needed for.
        from free_setup import build_runner, describe_chains

        runner, lm_configs = build_runner(
            config,
            args.output_dir,
            retriever=args.retriever
            or str(config.get_default("retriever", "duckduckgo")),
            only_providers=args.providers,
            use_cache=args.use_cache,
            # A dry run resolves the plan rather than running it, so it must
            # not need an API key: the point of it is to show what would
            # happen, which is exactly what someone without a key wants to
            # know.
            require_keys=not args.dry_run,
            **_overrides(args),
        )
    except Exception as exc:
        print(f"error: could not build the runner: {exc}", file=sys.stderr)
        return 2

    print(describe(config))
    print()
    print("Resolved provider chains:")
    chains = describe_chains(lm_configs)
    print(chains)
    print()

    if args.dry_run:
        print("Dry run: no API calls were made.")
        return 0

    # The run log records what happened, so that a run which fails partway
    # through still leaves a record of which providers were tried and why.
    run_log: RunLog | None = None
    capture: logging.Handler | None = None
    if not args.no_log:
        run_log = build_run_log(
            args.topic, path=args.log_file, base_dir=PROJECT_ROOT / "logs" / "runs"
        ).open()
        capture = attach_log_capture(run_log)
        run_log.write(f"command: {' '.join(sys.argv)}")
        run_log.write(f"config: {args.config}")
        run_log.write_block("provider chains", chains)

    try:
        return _run_pipeline(args, runner, run_log)
    finally:
        if capture is not None:
            detach_log_capture(capture)
        if run_log is not None:
            run_log.close()
            print(f"\nRun log: {run_log.path}")


def _run_pipeline(
    args: argparse.Namespace,
    runner: Any,
    run_log: RunLog | None,
) -> int:
    """Run the pipeline stages, recording the outcome in the run log."""
    stages_requested = (
        args.do_research
        or args.do_generate_outline
        or args.do_generate_article
        or args.do_polish_article
    )
    if not stages_requested:
        args.do_research = True
        args.do_generate_outline = True
        args.do_generate_article = True
        args.do_polish_article = True
        print("No stage flags given; running the full pipeline.\n")

    callback = CLICallbackHandler(verbose=args.verbose)
    print(f"Researching: {args.topic}\n")

    try:
        runner.run(
            topic=args.topic,
            do_research=args.do_research,
            do_generate_outline=args.do_generate_outline,
            do_generate_article=args.do_generate_article,
            do_polish_article=args.do_polish_article,
            callback_handler=callback,
        )
        runner.post_run()
    except KeyboardInterrupt:
        print(
            "\nInterrupted. Partial results are in the output directory.",
            file=sys.stderr,
        )
        if run_log is not None:
            run_log.write("run interrupted")
        return 130
    except Exception as exc:
        print(f"\nerror: the pipeline failed: {exc}", file=sys.stderr)
        hint = _quota_hint(exc)
        if hint:
            print(f"\n{hint}", file=sys.stderr)
        logger.exception("Pipeline failed")
        if run_log is not None:
            run_log.write(f"run failed: {type(exc).__name__}: {exc}")
        return 1

    article_dir = getattr(runner, "article_output_dir", None)
    if article_dir:
        print(f"\nArticle written to: {article_dir}")
        polished = Path(article_dir) / "storm_gen_article_polished.txt"
        if polished.exists():
            print(f"  {polished}")
        plain = Path(article_dir) / "storm_gen_article.txt"
        if plain.exists():
            print(f"  {plain}")
        if run_log is not None:
            run_log.write(f"article: {article_dir}")

    if run_log is not None:
        _record_run_summary(runner, run_log)

    return 0


def _record_run_summary(runner: Any, run_log: RunLog) -> None:
    """Record token usage and provider statistics in the run log.

    Upstream's ``Engine.summary()`` prints its report and returns ``None``,
    so it cannot be used to fill the run log.  The wrapper's own tracker
    holds the same information, so the summary is built from that instead.
    """
    lines: list[str] = []
    elapsed = getattr(runner, "time", None)
    if isinstance(elapsed, dict) and elapsed:
        lines.append("Execution time:")
        for stage, seconds in elapsed.items():
            lines.append(f"    {stage}: {seconds:.4f} seconds")
        lines.append("")

    usage: dict[str, dict[str, int]] = {}
    lm_configs = getattr(runner, "lm_configs", None)
    # Upstream keeps the language models in ``*_lm`` attributes of the
    # config object and walks them the same way when it collects usage.
    for attr_name in dir(lm_configs) if lm_configs is not None else []:
        if "_lm" not in attr_name:
            continue
        lm = getattr(lm_configs, attr_name, None)
        getter = getattr(lm, "usage_summary", None)
        if not callable(getter):
            continue
        try:
            for model, tokens in getter().items():
                entry = usage.setdefault(
                    model,
                    {
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": 0,
                        "calls": 0,
                    },
                )
                for key, value in tokens.items():
                    entry[key] = entry.get(key, 0) + value
        except Exception:
            logger.debug("Could not read usage summary", exc_info=True)

    if usage:
        lines.append("Token usage of language models:")
        for model, tokens in sorted(usage.items()):
            lines.append(
                f"    {model}: {tokens['prompt_tokens']} prompt + "
                f"{tokens['completion_tokens']} completion = "
                f"{tokens['total_tokens']} tokens "
                f"over {tokens['calls']} call(s)"
            )
        total = sum(t["total_tokens"] for t in usage.values())
        lines.append(f"    total: {total} tokens")
        lines.append("")

    if lines:
        run_log.write_block("summary", "\n".join(lines).rstrip())


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point."""
    args = build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    if args.list_providers:
        return cmd_list_providers()
    if args.doctor:
        _load_secrets(Path(args.secrets))
        return cmd_doctor(args)

    # Everything below this point needs the upstream STORM clone.  Check for
    # it up front so a fresh clone reports what to do instead of failing
    # with a bare ModuleNotFoundError pointing at an internal file.
    try:
        require_upstream()
    except UpstreamMissingError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    return cmd_run(args)


if __name__ == "__main__":
    sys.exit(main())
