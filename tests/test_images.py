"""Images as records: PNG and JPEG files judged as pictures, against a fake Decisions endpoint."""

import base64
import io
import json

import httpx
import pytest
from PIL import Image as Pil

from jgrep.cli import main

RED, BLUE = (220, 20, 20), (20, 40, 220)


def picture(path, color, fmt="PNG"):
    Pil.new("RGB", (400, 300), color).save(path, fmt)
    return str(path)


class Fake:
    """Says yes (0.9) when the picture it is shown is mostly red, else 0.1."""

    def __init__(self):
        self.bodies = []

    async def __call__(self, request):
        body = json.loads(request.content)
        self.bodies.append(body)
        part = body["state"]["image"] if isinstance(body["state"], dict) else None
        red = False
        if isinstance(part, dict):
            data = base64.b64decode(part["image_url"]["url"].split(",", 1)[1])
            r, g, b = Pil.open(io.BytesIO(data)).convert("RGB").getpixel((200, 150))
            red = r > 150 and b < 100
        answers = {qid: {"type": "noul", "noul": 0.9 if red else 0.1} for qid in body["questions"]}
        usage = {"input_tokens": 400, "output_tokens": 1, "cost": 0.0001}
        return httpx.Response(200, json={"model": "typesafe/jev-1.14", "answers": answers, "usage": usage})


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    for name in ("TYPESAFE_API_KEY", "JEV_API", "JEV_MODEL", "JEV_URL", "JEV_BUDGET"):
        monkeypatch.delenv(name, raising=False)


def jgrep(argv, stdin=None, monkeypatch=None):
    fake = Fake()
    out, err = io.StringIO(), io.StringIO()
    code = main(argv, transport=httpx.MockTransport(fake), out=out, err=err)
    return code, out.getvalue(), err.getvalue(), fake


def test_image_files_on_the_command_line_are_judged_as_pictures(tmp_path):
    red = picture(tmp_path / "red.png", RED)
    blue = picture(tmp_path / "blue.jpg", BLUE, "JPEG")
    code, out, err, fake = jgrep(["something red", red, blue])
    assert code == 0, err
    assert out == f"{red}\n"
    question = fake.bodies[0]["questions"]["d0"]["instructions"]
    assert question == 'The picture in `image` fits this description: "something red"'


def test_probabilities_and_json_name_the_file(tmp_path):
    red = picture(tmp_path / "red.png", RED)
    code, out, _, _ = jgrep(["-o", "something red", red])
    assert out == f"0.900\t{red}\n"
    code, out, _, _ = jgrep(["--json", "something red", red])
    assert json.loads(out) == {"file": red, "line": 1, "p": 0.9, "image": True}


def test_images_and_text_mix_in_one_run(tmp_path):
    red = picture(tmp_path / "red.png", RED)
    notes = tmp_path / "notes.txt"
    notes.write_text("plain words\n")
    code, out, err, fake = jgrep(["-o", "something red", str(notes), red])
    assert out.splitlines() == [f"0.900\t{red}"]
    assert fake.bodies[0]["state"] == "plain words"


def test_with_images_a_text_input_lists_image_paths(tmp_path):
    red = picture(tmp_path / "red.png", RED)
    blue = picture(tmp_path / "blue.png", BLUE)
    listing = tmp_path / "list.txt"
    listing.write_text(f"{blue}\n\n{red}\n")
    code, out, err, _ = jgrep(["--images", "something red", str(listing)])
    assert code == 0, err
    assert out == f"{red}\n"


def test_recursive_search_skips_images_unless_asked(tmp_path):
    folder = tmp_path / "photos"
    folder.mkdir()
    red = picture(folder / "red.png", RED)
    code, out, _, fake = jgrep(["-r", "something red", str(folder)])
    assert code == 1 and fake.bodies == []
    code, out, _, _ = jgrep(["-r", "--images", "something red", str(folder)])
    assert out == f"{red}\n"


def test_an_unreadable_image_path_is_reported_and_the_rest_continue(tmp_path):
    red = picture(tmp_path / "red.png", RED)
    listing = tmp_path / "list.txt"
    listing.write_text(f"{tmp_path / 'missing.png'}\n{red}\n")
    code, out, err, _ = jgrep(["--images", "something red", str(listing)])
    assert code == 2
    assert out == f"{red}\n"
    assert "cannot read image" in err


def test_a_provider_without_images_stops_with_a_clear_error(tmp_path, monkeypatch):
    red = picture(tmp_path / "red.png", RED)
    code, out, err, fake = jgrep(["--api", "laya", "something red", red])
    assert code == 2 and fake.bodies == []
    assert "laya does not read images" in err


def test_images_cannot_combine_with_text_modes(tmp_path):
    code, _, err, _ = jgrep(["--images", "--para", "x", "-"])
    assert code == 2 and "--images cannot be combined" in err


def test_estimate_counts_image_tokens(tmp_path):
    red = picture(tmp_path / "red.png", RED)
    code, out, err, fake = jgrep(["--estimate", "--json", "something red", red])
    assert code == 0, err
    result = json.loads(out)
    assert result["estimated_calls"] == 1 and result["estimated_input_tokens"] > 300
    assert fake.bodies == []
