from mihomo_proxy_manager.route_targets import (
    COMPANION_TARGETS,
    QuerySelection,
    canonical_target_for_format,
    has_future_user_agent_signal,
    normalize_target_alias,
    resolve_query_selection,
    resolve_user_agent_format,
)


def test_resolve_query_selection_uses_first_present_key_and_first_value() -> None:
    selection = resolve_query_selection(
        {
            "target": ["surfboard", "quanx"],
            "format": ["v2rayn"],
        }
    )

    assert selection == QuerySelection(format="surfboard", explicit=True)


def test_resolve_query_selection_prioritizes_format_over_flag_and_client() -> None:
    selection = resolve_query_selection(
        {
            "format": ["quanx"],
            "flag": ["meta"],
            "client": ["v2rayn"],
        }
    )

    assert selection == QuerySelection(format="quantumult-x", explicit=True)


def test_resolve_query_selection_prioritizes_flag_over_client() -> None:
    selection = resolve_query_selection(
        {
            "flag": ["meta"],
            "client": ["v2rayn"],
        }
    )

    assert selection == QuerySelection(format="provider", explicit=True)


def test_blank_query_selector_suppresses_lower_priority_keys() -> None:
    selection = resolve_query_selection(
        {
            "target": [""],
            "format": ["quanx"],
        }
    )

    assert selection == QuerySelection(format=None, explicit=False)


def test_whitespace_query_selector_suppresses_lower_priority_keys() -> None:
    selection = resolve_query_selection(
        {
            "target": ["   "],
            "format": ["quanx"],
        }
    )

    assert selection == QuerySelection(format=None, explicit=False)


def test_auto_query_selector_means_no_explicit_target() -> None:
    selection = resolve_query_selection(
        {
            "target": ["auto"],
            "format": ["quanx"],
        }
    )

    assert selection == QuerySelection(format=None, explicit=False)


def test_query_aliases_are_trimmed_case_insensitive_and_underscore_normalized() -> None:
    assert resolve_query_selection({"target": [" Quantumult_X "]}).format == (
        "quantumult-x"
    )
    assert resolve_query_selection({"target": ["clash-meta"]}).format == "provider"
    assert resolve_query_selection({"target": ["clash.meta"]}).format == "provider"
    assert resolve_query_selection({"target": ["provider"]}).format == "provider"
    assert resolve_query_selection({"target": ["mihomo"]}).format == "provider"
    assert resolve_query_selection({"target": ["meta"]}).format == "provider"
    assert resolve_query_selection({"target": ["v2rayN"]}).format == "xray-uri"


def test_alias_normalization_uses_already_decoded_http_parser_value() -> None:
    assert normalize_target_alias("clash_meta") == "clash-meta"
    assert normalize_target_alias("clash.meta") == "clash.meta"
    assert normalize_target_alias("clash%252Dmeta") == "clash%252dmeta"


def test_query_alias_resolver_does_not_double_decode_http_parser_values() -> None:
    from starlette.datastructures import QueryParams

    once_decoded = QueryParams("target=clash%2Dmeta").getlist("target")
    double_encoded = QueryParams("target=clash%252Dmeta").getlist("target")

    assert resolve_query_selection({"target": once_decoded}).format == "provider"
    selection = resolve_query_selection({"target": double_encoded})
    assert selection.format is None
    assert selection.explicit is True
    assert selection.unsupported == "clash%2dmeta"


def test_reserved_future_query_target_is_explicit_but_unimplemented() -> None:
    selection = resolve_query_selection({"target": ["singbox"]})

    assert selection.format == "sing-box"
    assert selection.explicit is True


def test_unknown_query_target_is_explicit_unknown() -> None:
    selection = resolve_query_selection({"target": ["not-a-client"]})

    assert selection.format is None
    assert selection.explicit is True
    assert selection.unsupported == "not-a-client"


def test_user_agent_matching_is_case_insensitive() -> None:
    assert resolve_user_agent_format("quantumult x/1.0") == "quantumult-x"
    assert resolve_user_agent_format("surfboard/2.0") == "surfboard"
    assert resolve_user_agent_format("V2RAYN/6.0") == "xray-uri"
    assert resolve_user_agent_format("flclash/1.0") == "provider"


def test_surfboard_profile_fetcher_user_agent_matches() -> None:
    """Test real Surfboard Profile Fetcher UA resolves to surfboard format."""
    ua = (
        "Surfboard Profile Fetcher/mobile-2.25.3 (Build 261) "
        "Dalvik/2.1.0 (Linux; U; Android 15; PJZ110 Build/AP3A.240617.008)"
    )
    assert resolve_user_agent_format(ua) == "surfboard"


def test_specific_xray_user_agent_beats_broad_provider_signal() -> None:
    assert resolve_user_agent_format("v2rayN meta") == "xray-uri"


def test_future_user_agent_signal_does_not_mask_implemented_signal() -> None:
    assert resolve_user_agent_format("sing-box Clash") == "provider"


def test_only_future_user_agent_signal_returns_none() -> None:
    assert resolve_user_agent_format("sing-box/1.0") is None
    assert has_future_user_agent_signal("sing-box/1.0") is True
    assert has_future_user_agent_signal("unknown-client/1.0") is False


def test_canonical_targets() -> None:
    assert canonical_target_for_format("provider") == "clash"
    assert canonical_target_for_format("surfboard") == "surfboard"
    assert canonical_target_for_format("quantumult-x") == "quanx"
    assert canonical_target_for_format("xray-uri") == "v2rayn"


def test_companion_targets() -> None:
    assert COMPANION_TARGETS == {"nodes": "surfboard", "import": "quantumult-x"}
