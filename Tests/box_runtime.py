"""Real MCP SDK + Codex calls to the isolated installed-ZIP GUI box fixture."""

import asyncio
import argparse
import json
from pathlib import Path
import re
import socket
import sys
import time
import uuid

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime"
DESCRIPTOR = RUNTIME / "box-session.json"


async def control(action, **kwargs):
    nonce = uuid.uuid4().hex
    temporary = RUNTIME / "box-control.tmp"
    temporary.write_text(json.dumps(dict(nonce=nonce, action=action, **kwargs)), encoding="utf-8")
    temporary.replace(RUNTIME / "box-control.json")
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            result = json.loads((RUNTIME / "box-state.json").read_text(encoding="utf-8"))
            if result["nonce"] == nonce:
                assert result["success"], result
                return {k: v for k, v in result.items() if k != "nonce"}
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        await asyncio.sleep(0.1)
    raise TimeoutError("Isolated Blender fixture did not acknowledge control")


def nonfinite_bridge(value):
    descriptor = json.loads(DESCRIPTOR.read_text(encoding="utf-8"))
    with socket.create_connection(("127.0.0.1", descriptor["port"]), timeout=5) as client:
        client.sendall((json.dumps({"command": "create_box_at_cursor", "token": descriptor["token"],
                                   "size_x": value, "size_y": 2, "size_z": 3}) + "\n").encode())
        response = b""
        while b"\n" not in response:
            chunk = client.recv(4096)
            if not chunk:
                raise RuntimeError("Missing response; no retry")
            response += chunk
    return json.loads(response)


async def run_real_sol_feedback():
    python = (ROOT / ".venv/Scripts/python.exe").as_posix()
    server = (ROOT / "MCP/server.py").as_posix()
    descriptor = DESCRIPTOR.as_posix()
    feedback_log = (RUNTIME / "box-agent-feedback.jsonl").as_posix()
    config_args = json.dumps([server, "--session-file", descriptor, "--feedback-log", feedback_log])
    prompt = (
        "Проверь Agent Feedback реального Astro Modeler. Не читай файлы и persistent feedback log. "
        "Вызови только post_modeling_note ровно четыре раза по порядку: "
        "OK / summary 'SOL OK' / details 'Операция завершена'; "
        "WARNING / summary 'SOL WARNING' / details 'Есть существенная неопределённость'; "
        "BLOCKED / summary 'SOL BLOCKED' / details 'Нужен инструмент локального измерения толщины'; "
        "OK / summary 'SOL LONG RU' / details из длинного русского текста не короче 600 символов. "
        "Не вызывай другие tools. После четырёх подтверждённых вызовов кратко сообщи результат."
    )
    process = await asyncio.create_subprocess_exec(
        "codex", "exec", "--ephemeral", "--ignore-user-config", "--ignore-rules",
        "--sandbox", "read-only", "--model", "gpt-5.6-sol",
        "-c", 'model_reasoning_effort="medium"',
        "-c", f'mcp_servers.astro_modeler.command="{python}"',
        "-c", f"mcp_servers.astro_modeler.args={config_args}",
        "-c", f'mcp_servers.astro_modeler.cwd="{ROOT.as_posix()}"',
        "-C", str(ROOT), prompt,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await process.communicate()
    (RUNTIME / "sol-feedback-runtime.log").write_bytes(output)
    assert process.returncode == 0, output.decode("utf-8", errors="replace")


async def run_real_sol_modifier():
    python = (ROOT / ".venv/Scripts/python.exe").as_posix()
    server = (ROOT / "MCP/server.py").as_posix()
    descriptor = DESCRIPTOR.as_posix()
    final_path = RUNTIME / "sol-modifier-final.txt"
    final_path.unlink(missing_ok=True)
    config_args = json.dumps([server, "--session-file", descriptor])
    prompt = (
        "Вызови только inspect_selected_modifier_changes ровно один раз. Затем объясни результат "
        "простым русским языком для начинающего согласно instruction/context из tool result. "
        "Не добавляй changed properties, которых нет в результате. Отделяй доказанные значения "
        "от вероятной причины. Последней строкой напиши строго: "
        "FACT_PROPERTIES: width, segments, offset_type"
    )
    process = await asyncio.create_subprocess_exec(
        "codex", "exec", "--ephemeral", "--ignore-user-config", "--ignore-rules",
        "--sandbox", "read-only", "--model", "gpt-5.6-sol",
        "-c", 'model_reasoning_effort="low"', "--output-last-message", str(final_path),
        "-c", f'mcp_servers.astro_modeler.command="{python}"',
        "-c", f"mcp_servers.astro_modeler.args={config_args}",
        "-c", f'mcp_servers.astro_modeler.cwd="{ROOT.as_posix()}"',
        "-C", str(ROOT), prompt,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await process.communicate()
    (RUNTIME / "sol-modifier-runtime.log").write_bytes(output)
    assert process.returncode == 0, output.decode("utf-8", errors="replace")
    final = final_path.read_text(encoding="utf-8")
    assert "FACT_PROPERTIES: width, segments, offset_type" in final
    assert "0.003" in final and "6" in final
    assert any(word in final.lower() for word in ("вероят", "обычно", "мог"))


async def main(real_sol=False, visual_only=False, compact_ui=False, version_only=False,
               activity_ui=False, modifier_inspector=False, format_only=False,
               identity_only=False):
    if identity_only:
        state = await control("modifier_identity")
        await control("finish")
        print(json.dumps({"success": True, "targeted": "modifier runtime identity",
                          "full_version": state["full_version"],
                          "stale_replacement_rejected": state["stale_replacement_rejected"],
                          "targets": [state["unchanged_target"], state["same_type_target"]]},
                         ensure_ascii=False))
        return
    params = StdioServerParameters(command=sys.executable,
        args=[str(ROOT / "MCP/server.py"), "--session-file", str(DESCRIPTOR),
              "--feedback-log", str(RUNTIME / "box-agent-feedback.jsonl")])
    evidence = {"cases": [], "source": "real GUI Blender, installed ZIP, SDK STDIO"}
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            names = {t.name for t in (await session.list_tools()).tools}
            assert names == {"create_cube", "get_selected_context", "create_box_at_cursor", "post_modeling_note", "inspect_selected_modifier_changes"}
            if version_only:
                state = await control("version_check")
                await control("finish")
                print(json.dumps({"success": True, "full_version": state["full_version"]},
                                 ensure_ascii=False))
                return
            if format_only:
                state = await control("modifier_format")
                await control("finish")
                print(json.dumps({"success": True, "targeted": "modifier RU UI and float format",
                                  "full_version": state["full_version"]}, ensure_ascii=False))
                return
            if modifier_inspector:
                prepared = await control("modifier_prepare")
                assert len(prepared["modifier_targets"]) == 2
                compared = await control("modifier_compare")
                assert compared["inspector_message"] == "Изменено параметров: 3"
                response = await session.call_tool("inspect_selected_modifier_changes", {})
                assert response.structuredContent["success"]
                inspection = response.structuredContent["inspection"]
                assert [item["property"] for item in inspection["changed_properties"]] == [
                    "width", "segments", "offset_type"]
                assert inspection["user_context"] == "Объясни как начинающему."
                assert inspection["explanation_instruction"]
                if real_sol:
                    await run_real_sol_modifier()
                await control("finish")
                print(json.dumps({"success": True, "targeted": "modifier inspector",
                                  "changed": ["width", "segments", "offset_type"],
                                  "dirty": [compared["dirty_before"], compared["dirty_after"]],
                                  "real_sol": real_sol}, ensure_ascii=False))
                return
            if activity_ui:
                await control("clear_activity")
                before = await control("snapshot")
                context = await session.call_tool("get_selected_context", {})
                assert context.structuredContent["success"]
                note = await session.call_tool("post_modeling_note", {
                    "status": "OK", "summary": "Activity runtime check", "details": ""})
                assert note.structuredContent["success"]
                error = await session.call_tool("create_box_at_cursor", {
                    "size_x": 1e40, "size_y": 3, "size_z": 4})
                assert not error.structuredContent["success"]
                activity = await control("activity_state")
                assert activity["activity_counts"] == {
                    "get_selected_context": 1,
                    "post_modeling_note": 1,
                    "create_box_at_cursor": 1,
                }
                assert activity["last_activity"]["tool_name"] == "create_box_at_cursor"
                assert activity["last_activity"]["outcome"] == "ERROR"
                assert activity["hud_text"] == "Astro Modeler · create_box_at_cursor"
                assert activity["hud_handler"]
                timeout = await control("activity_timeout")
                assert timeout["hud_text"] == "Astro Modeler · create_box_at_cursor"
                changed = await control("activity_settings", show_hud=False, text_size=31,
                                        text_color=[0.1, 0.8, 0.2, 1.0], vertical_position=35)
                assert changed["hud_settings"]["show_hud"] is False
                assert changed["hud_settings"]["text_size"] == 31
                assert changed["hud_settings"]["vertical_position"] == 35
                assert await control("snapshot") == before
                cleared = await control("clear_activity")
                assert not cleared["activity_counts"] and cleared["last_activity"] is None
                assert await control("snapshot") == before
                await control("finish")
                print(json.dumps({"success": True, "targeted": "agent activity HUD",
                                  "tools": 4}, ensure_ascii=False))
                return
            if compact_ui:
                await control("prepare_feedback")
                before = await control("snapshot")
                result = await session.call_tool("post_modeling_note", {
                    "status": "BLOCKED", "summary": "Нужен controlled mesh-analysis tool", "details": ""})
                assert result.structuredContent["success"], result
                long_note = {
                    "status": "WARNING",
                    "summary": "Проверка минимальной толщины недоступна",
                    "details": ("Blender должен вычислить численную толщину детерминированным алгоритмом. "
                                "Codex Agent интерпретирует компактный результат, оценивает риск и объясняет "
                                "следующий шаг пользователю, не повторяя summary в details."),
                }
                for _ in range(2):
                    result = await session.call_tool("post_modeling_note", long_note)
                    assert result.structuredContent["success"], result
                feedback = await control("feedback_state")
                assert len(feedback["notes"]) == 2
                assert feedback["notes"][0]["repeat_count"] == 2
                await control("toggle_first_feedback")
                assert await control("snapshot") == before
                await control("finish")
                print(json.dumps({"success": True, "targeted": "compact feedback UI",
                                  "clusters": 2}, ensure_ascii=False))
                return
            if visual_only:
                await control("prepare_feedback")
                duplicate = {"status": "WARNING", "summary": "Нет локального измерения толщины",
                             "details": "Нужен отдельный controlled tool для точного измерения."}
                for _ in range(3):
                    result = await session.call_tool("post_modeling_note", duplicate)
                    assert result.structuredContent["success"], result
                for note in (
                    {"status": "OK", "summary": "Промежуточная операция завершена", "details": ""},
                    duplicate,
                    {"status": "BLOCKED", "summary": "Зависимая ветка остановлена",
                     "details": "После одной полезной read-only диагностики требуется решение пользователя."},
                    {"status": "OK", "summary": "Длинный details доступен по кнопке",
                     "details": "Этот длинный русский текст показывает компактный preview в карточке. "
                                "Полное содержимое сохраняется и раскрывается локальной кнопкой Show full details. "
                                "Состояние раскрытия не хранится в Scene, Object properties, Text datablocks или .blend. "
                                "Повторное нажатие скрывает полный текст и возвращает компактный вид."},
                ):
                    result = await session.call_tool("post_modeling_note", note)
                    assert result.structuredContent["success"], result
                feedback = await control("feedback_state")
                assert feedback["notes"][-1]["repeat_count"] == 3
                print(json.dumps({"success": True, "visual_check_ready": True,
                                  "clusters": len(feedback["notes"])}, ensure_ascii=False))
                return
            for label, position, rotation, sizes in (
                ("origin", [0, 0, 0], [0, 0, 0], [20, 10, 5]),
                ("translated", [7, -3, 2.5], [0, 0, 0], [3, 4, 7]),
                ("cursor_rotation_ignored", [-4, 6, 2], [.3, -.7, 1.2], [2, 5, 9]),
                ("repeat_and_unit_scale_ignored", [-4, 6, 2], [.3, -.7, 1.2], [.25, 2.5, 12]),
            ):
                before = await control("configure", position=position, rotation=rotation, unit_scale=.001)
                result = await session.call_tool("create_box_at_cursor", dict(zip(("size_x", "size_y", "size_z"), sizes)))
                assert result.structuredContent["success"], result
                after = await control("snapshot")
                assert after["objects"] == before["objects"] + 1
                context = (await session.call_tool("get_selected_context", {})).structuredContent
                assert context["success"] and context["context"]["active_object"]["name"] == result.structuredContent["object_name"]
                evidence["cases"].append(label)
            before = await control("snapshot")
            for value in (0, -1, True, "2"):
                result = await session.call_tool("create_box_at_cursor", {"size_x": value, "size_y": 2, "size_z": 3})
                assert result.isError, result
            for value in (float("nan"), float("inf"), -float("inf")):
                assert not (await asyncio.to_thread(nonfinite_bridge, value))["success"]
            # Finite inputs outside Blender float32 mesh range fail in Blender itself.
            for value in (1e40, 1e-50):
                result = await session.call_tool("create_box_at_cursor", {"size_x": value, "size_y": 2, "size_z": 3})
                assert not result.structuredContent["success"]
            assert await control("snapshot") == before
            evidence["cases"].append("invalid/nonfinite/range: no object or mesh allocated")
            before = await control("configure", position=[1, 2, 3], edit=True)
            result = await session.call_tool("create_box_at_cursor", {"size_x": 2, "size_y": 3, "size_z": 4})
            assert not result.structuredContent["success"] and "Object Mode" in result.structuredContent["message"]
            assert await control("snapshot") == before
            evidence["cases"].append("Edit Mode rejected unchanged")
            before = await control("configure", position=[8, -3, 1], rotation=[.4, .7, -.5])
            process = await asyncio.create_subprocess_exec(sys.executable, str(ROOT / "Tests/codex_runtime.py"), "--box")
            assert await process.wait() == 0
            after = await control("snapshot")
            assert after["objects"] == before["objects"] + 1 and after["box_calls"] == before["box_calls"] + 1
            evidence["cases"].append("installed Codex app-server: one Box, independent fixture checks")
            result = await session.call_tool("create_cube", {})
            assert result.structuredContent["success"]
            await control("cube_check")
            context = (await session.call_tool("get_selected_context", {})).structuredContent
            assert context["context"]["active_object"]["name"] == result.structuredContent["object_name"]
            evidence["cases"].append("Create Cube / Selected Context regression")
            await control("prepare_feedback")
            before = await control("snapshot")
            duplicate = {"status": "WARNING", "summary": "Нет измерения",
                         "details": "Нужен инструмент локального измерения толщины."}
            for _ in range(100):
                result = await session.call_tool("post_modeling_note", duplicate)
                assert result.structuredContent["success"], result
            feedback = await control("feedback_state")
            assert len(feedback["notes"]) == 1 and feedback["notes"][0]["repeat_count"] == 100
            assert feedback["notes"][0]["first_time"] <= feedback["notes"][0]["last_time"]
            result = await session.call_tool(
                "post_modeling_note", {"status": "OK", "summary": "Другая запись", "details": ""})
            assert result.structuredContent["success"]
            result = await session.call_tool("post_modeling_note", duplicate)
            assert result.structuredContent["success"]
            feedback = await control("feedback_state")
            assert len(feedback["notes"]) == 3
            assert [note["repeat_count"] for note in feedback["notes"]] == [1, 1, 100]
            persistent = [json.loads(line) for line in (RUNTIME / "box-agent-feedback.jsonl").read_text(encoding="utf-8").splitlines()]
            assert persistent[-3]["repeat_count"] == 100
            assert [entry["summary"] for entry in persistent[-3:]] == [
                "Нет измерения", "Другая запись", "Нет измерения"]
            await control("clear_feedback")
            for index in range(21):
                status = ("OK", "WARNING", "BLOCKED")[index % 3]
                details = ("Не хватает измерения толщины. Рекомендация: локальный анализ. " * 20) if index == 1 else ""
                result = await session.call_tool("post_modeling_note", {"status": status, "summary": f"Итог {index}", "details": details})
                assert result.structuredContent["success"], result
            feedback = await control("feedback_state")
            assert [n["summary"] for n in feedback["notes"]] == [f"Итог {i}" for i in range(20, 0, -1)]
            assert len(feedback["notes"][-1]["details"]) > 1000 and not feedback["dirty"]
            assert all(re.fullmatch(r"\d{2}:\d{2}:\d{2}", n["first_time"]) for n in feedback["notes"])
            assert feedback["notes"][0]["details"] == ""
            await control("toggle_first_feedback")
            for args in ({"status": "BAD", "summary": "Итог"}, {"status": "OK", "summary": ""}, {"status": "OK", "summary": " "}):
                result = await session.call_tool("post_modeling_note", args)
                assert result.isError or not result.structuredContent["success"]
            result = await session.call_tool("post_modeling_note", {"status": "WARNING", "summary": "Итог", "details": "界" * 1500})
            assert not result.structuredContent["success"] and "4096 UTF-8 bytes" in result.structuredContent["message"]
            assert (await control("feedback_state"))["note_checks"] == 123
            assert await control("snapshot") == before
            process = await asyncio.create_subprocess_exec(sys.executable, str(ROOT / "Tests/codex_runtime.py"), "--note")
            assert await process.wait() == 0
            feedback = await control("feedback_state")
            assert feedback["notes"][0]["summary"] == "Проверка канала Codex" and feedback["note_checks"] == 124
            assert not feedback["dirty"]
            if real_sol:
                await run_real_sol_feedback()
                feedback = await control("feedback_state")
                assert [note["summary"] for note in feedback["notes"][:4]] == [
                    "SOL LONG RU", "SOL BLOCKED", "SOL WARNING", "SOL OK"]
                assert len(feedback["notes"][0]["details"]) >= 600
                evidence["cases"].append("real GPT-5.6 Sol MEDIUM: four MCP notes, all statuses, long Russian details, no log read requested")
            await control("clear_feedback")
            assert not (await control("feedback_state"))["notes"]
            result = await session.call_tool("post_modeling_note", {"status": "OK", "summary": "После очистки", "details": ""})
            assert result.structuredContent["success"]
            assert (await control("feedback_state"))["notes"][0]["summary"] == "После очистки"
            await control("feedback_save_load")
            assert not (await control("feedback_state"))["notes"]
            persistent = [json.loads(line) for line in (RUNTIME / "box-agent-feedback.jsonl").read_text(encoding="utf-8").splitlines()]
            assert persistent[-1]["summary"] == "После очистки" and len(persistent) <= 200
            evidence["cases"].append("Feedback: exact duplicate clusters (100→1), statuses, timestamps, long Russian preview/toggle state, rolling 20, empty details, Clear, bounded clustered JSONL, validation/byte limit, unchanged scene/dirty, no .blend persistence")
    await control("finish")
    evidence["cases"].append("ZIP install/enable, stop/restart, load disconnect, disable cleanup")
    evidence["events"] = json.loads((RUNTIME / "box-events.json").read_text(encoding="utf-8"))
    evidence["success"] = True
    (RUNTIME / "box-result.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps({"success": True, "cases": evidence["cases"], "boxes_verified": len(evidence["events"])}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--real-sol", action="store_true")
    parser.add_argument("--visual-only", action="store_true")
    parser.add_argument("--compact-ui", action="store_true")
    parser.add_argument("--version-only", action="store_true")
    parser.add_argument("--activity-ui", action="store_true")
    parser.add_argument("--modifier-inspector", action="store_true")
    parser.add_argument("--format-only", action="store_true")
    parser.add_argument("--identity-only", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.real_sol, args.visual_only, args.compact_ui, args.version_only,
                     args.activity_ui, args.modifier_inspector, args.format_only,
                     args.identity_only))
