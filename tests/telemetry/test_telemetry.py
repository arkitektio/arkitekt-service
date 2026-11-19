"""Telemetry end to end: a served request, a real collector, and what jaeger then holds."""

import secrets
import time

import httpx

from tests.telemetry.conftest import Stack


def ask(url: str, query: str, **headers: str) -> dict:
    response = httpx.post(f"{url}/graphql", json={"query": query}, headers=headers, timeout=10)
    assert response.status_code == 200, response.text
    return response.json()


def a_trace() -> tuple[str, str]:
    """A trace somebody else started: its id, and the header a request inside it carries."""
    trace_id = secrets.token_hex(16)
    return trace_id, f"00-{trace_id}-{secrets.token_hex(8)}-01"


def spans_of(stack: Stack, trace_id: str, at_least: int, timeout: float = 20.0) -> dict[str, dict]:
    """The trace's spans as jaeger has them, by operation name, once there are ``at_least``."""
    deadline = time.monotonic() + timeout
    found: list[dict] = []
    while time.monotonic() < deadline:
        response = httpx.get(f"{stack.jaeger}/api/traces/{trace_id}", timeout=5)
        if response.status_code == 200:
            trace = response.json()["data"][0]
            found = [{**span, "service": trace["processes"][span["processID"]]["serviceName"]} for span in trace["spans"]]
            if len(found) >= at_least:
                break
        time.sleep(0.3)
    assert len(found) >= at_least, f"jaeger holds {[span['operationName'] for span in found]} of trace {trace_id}"
    return {span["operationName"]: span for span in found}


def tags(span: dict) -> dict[str, object]:
    return {tag["key"]: tag["value"] for tag in span["tags"]}


def parent_of(span: dict) -> str | None:
    return next((reference["spanID"] for reference in span["references"] if reference["refType"] == "CHILD_OF"), None)


def test_a_request_is_one_trace_of_request_operation_and_statement(stack: Stack, recording: str) -> None:
    trace_id, traceparent = a_trace()
    assert ask(recording, "query Counted { one }", traceparent=traceparent) == {"data": {"one": 1}}

    spans = spans_of(stack, trace_id, at_least=3)
    request, operation, statement = spans["POST /graphql"], spans["graphql Counted"], spans["SELECT"]
    assert {span["service"] for span in spans.values()} == {"served"}, "named as the contract names the service"
    assert parent_of(request) == traceparent.split("-")[2], "the request continues the trace it arrived in"
    assert parent_of(operation) == request["spanID"]
    assert parent_of(statement) == operation["spanID"], "what an operation does is inside its span"
    assert tags(operation)["graphql.operation.type"] == "query"
    assert set(spans) == {"POST /graphql", "graphql Counted", "SELECT"}, "nothing per field, per receive or per send"


def test_an_operation_that_fails_is_marked(stack: Stack, recording: str) -> None:
    trace_id, traceparent = a_trace()
    assert ask(recording, "query Broken { fails }", traceparent=traceparent)["errors"]

    operation = spans_of(stack, trace_id, at_least=2)["graphql Broken"]
    assert tags(operation).get("error") is True


def test_what_the_service_asks_of_others_is_in_the_trace(stack: Stack, recording: str) -> None:
    trace_id, traceparent = a_trace()
    answer = ask(recording, f'query Asking {{ asked(url: "{recording}/echo") }}', traceparent=traceparent)

    assert answer["data"]["asked"].split("-")[1] == trace_id
    spans = spans_of(stack, trace_id, at_least=4)
    assert {"POST /graphql", "graphql Asking", "GET", "GET /echo"} <= set(spans)
    assert parent_of(spans["GET /echo"]) == spans["GET"]["spanID"], "the request it made, and the request that arrived, are parent and child"


def test_the_health_check_is_not_traced(stack: Stack, recording: str) -> None:
    trace_id, traceparent = a_trace()
    httpx.get(f"{recording}/ht", headers={"traceparent": traceparent}, timeout=5).raise_for_status()
    # Something after it that is traced: once that is in jaeger, the health check would be too.
    marker, marked = a_trace()
    ask(recording, "query After { one }", traceparent=marked)
    spans_of(stack, marker, at_least=3)

    assert httpx.get(f"{stack.jaeger}/api/traces/{trace_id}", timeout=5).status_code == 404


def test_operations_are_measured(stack: Stack, recording: str) -> None:
    ask(recording, "query Measured { one }")

    deadline = time.monotonic() + 20
    page = ""
    while time.monotonic() < deadline:
        page = httpx.get(stack.metrics, timeout=5).text
        if 'graphql_operation_name="Measured"' in page:
            break
        time.sleep(0.3)
    line = next((line for line in page.splitlines() if line.startswith("graphql_server_operation_duration_seconds_count") and 'graphql_operation_name="Measured"' in line), "")
    assert 'outcome="ok"' in line and 'graphql_operation_type="query"' in line, page[-2000:]


def test_without_a_collector_nothing_of_the_sdk_is_loaded(silent: str) -> None:
    assert ask(silent, "query Counted { one }") == {"data": {"one": 1}}
    assert httpx.get(f"{silent}/modules", timeout=5).json() == {"sdk": []}


def test_without_a_collector_a_trace_is_still_passed_on(silent: str) -> None:
    trace_id, traceparent = a_trace()
    answer = ask(silent, f'query Asking {{ asked(url: "{silent}/echo") }}', traceparent=traceparent)

    assert answer["data"]["asked"] == traceparent, "unchanged: this service added no span of its own"


def test_without_a_trace_nothing_is_passed_on(silent: str) -> None:
    answer = ask(silent, f'query Asking {{ asked(url: "{silent}/echo") }}')

    assert answer["data"]["asked"] is None
