"""PM runtime — executable CLI entry point (MVP-001, MVP-002).

Run it as::

    python -m pm propose "user text request" [--root DIR]
    python -m pm show PROP-<16 hex> [--root DIR]
    python -m pm approve PROP-<16 hex> --approver NAME [--note TEXT] [--root DIR]
    python -m pm propose-batch PROP-... [PROP-...] [--root DIR]
    python -m pm show-batch BATCH-<16 hex> [--root DIR]
    python -m pm approve-batch BATCH-<16 hex> --approver NAME [--note TEXT] [--root DIR]

Each successful command prints the machine-readable proposal or batch
record (JSON) on stdout; control failures are reported on stderr with
exit status 1 and never print a record.

Deliberately absent from this MVP: any command that dispatches or
executes implementation work.  The only implementation handoffs live
behind the API-level gates :meth:`pm.runtime.PMRuntime.handoff` and
:meth:`pm.runtime.PMRuntime.handoff_batch`, which refuse missing,
unapproved, and batch-unapproved records.  Backend adapters and any
dispatch automation belong to later MVP tasks and are out of scope
here.

The CLI owns no authority of its own: it only exposes the PM runtime,
so write/network/credential/external-action permissions are never
granted through it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from pm.errors import PMRuntimeError
from pm.runtime import PMRuntime
from protocol.artifacts import ArtifactValidationError


def build_parser() -> argparse.ArgumentParser:
    """Build the ``pm`` command-line parser."""

    parser = argparse.ArgumentParser(
        prog="pm",
        description=(
            "PM runtime — authoritative Project-Manager-side entry "
            "point (MVP-001 / MVP-002, Protocol V1.1)."
        ),
    )
    subparsers = parser.add_subparsers(
        dest="command", required=True, metavar="command"
    )

    propose_parser = subparsers.add_parser(
        "propose",
        help="turn a user text request into a structured PM proposal",
    )
    propose_parser.add_argument("request", help="the user text request")
    propose_parser.add_argument(
        "--root",
        default=None,
        help="project root holding project/ and MASTER_PLAN.md",
    )

    show_parser = subparsers.add_parser(
        "show", help="print one stored proposal as JSON"
    )
    show_parser.add_argument("proposal_id", help="proposal identity")
    show_parser.add_argument(
        "--root",
        default=None,
        help="project root holding project/ and MASTER_PLAN.md",
    )

    approve_parser = subparsers.add_parser(
        "approve",
        help="record the explicit PM approval for one stored proposal",
    )
    approve_parser.add_argument("proposal_id", help="proposal identity")
    approve_parser.add_argument(
        "--approver",
        required=True,
        help="PM-side identity granting the approval",
    )
    approve_parser.add_argument(
        "--note", default="", help="optional approval note"
    )
    approve_parser.add_argument(
        "--root",
        default=None,
        help="project root holding project/ and MASTER_PLAN.md",
    )

    propose_batch_parser = subparsers.add_parser(
        "propose-batch",
        help="group stored proposals into a deterministic PM batch",
    )
    propose_batch_parser.add_argument(
        "proposal_ids",
        nargs="+",
        help="one or more stored proposal identities",
    )
    propose_batch_parser.add_argument(
        "--root",
        default=None,
        help="project root holding project/ and MASTER_PLAN.md",
    )

    show_batch_parser = subparsers.add_parser(
        "show-batch", help="print one stored batch as JSON"
    )
    show_batch_parser.add_argument("batch_id", help="batch identity")
    show_batch_parser.add_argument(
        "--root",
        default=None,
        help="project root holding project/ and MASTER_PLAN.md",
    )

    approve_batch_parser = subparsers.add_parser(
        "approve-batch",
        help="record the explicit PM approval for one stored batch",
    )
    approve_batch_parser.add_argument("batch_id", help="batch identity")
    approve_batch_parser.add_argument(
        "--approver",
        required=True,
        help="PM-side identity granting the approval",
    )
    approve_batch_parser.add_argument(
        "--note", default="", help="optional approval note"
    )
    approve_batch_parser.add_argument(
        "--root",
        default=None,
        help="project root holding project/ and MASTER_PLAN.md",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the ``pm`` CLI; return the process exit status."""

    args = build_parser().parse_args(argv)
    runtime = PMRuntime(root=Path(args.root) if args.root else None)

    try:
        if args.command == "propose":
            print(runtime.propose(args.request).to_json())
        elif args.command == "show":
            print(runtime.get(args.proposal_id).to_json())
        elif args.command == "approve":
            print(
                runtime.approve(
                    args.proposal_id,
                    approver=args.approver,
                    note=args.note,
                ).to_json()
            )
        elif args.command == "propose-batch":
            print(runtime.propose_batch(args.proposal_ids).to_json())
        elif args.command == "show-batch":
            print(runtime.get_batch(args.batch_id).to_json())
        elif args.command == "approve-batch":
            print(
                runtime.approve_batch(
                    args.batch_id,
                    approver=args.approver,
                    note=args.note,
                ).to_json()
            )
        else:  # pragma: no cover - argparse rejects unknown commands
            raise PMRuntimeError(f"unknown command {args.command!r}")
    except (PMRuntimeError, ArtifactValidationError, OSError) as exc:
        print(f"pm: error: {exc}", file=sys.stderr)
        return 1
    return 0
