from mihomo_proxy_manager.cli import build_parser


def test_build_parser_has_expected_commands() -> None:
    parser = build_parser()
    choices = parser._subparsers._group_actions[0].choices  # type: ignore

    assert {"serve", "check", "refresh"} <= set(choices)  # type: ignore
