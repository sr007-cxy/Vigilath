from io import BytesIO
from types import SimpleNamespace

from PIL import Image

from geo.services import image_gen


EXPECTED_PROMPT = (
    "请以输入图片中的真实地点和内容为参考，生成同一地点的一张全新摄影图片。"
    "准确保留原图中的场所身份、建筑结构、品牌特征、主要设施和整体风格，"
    "不得虚构不存在的设施，不得改变店名、品牌或关键事实。"
    "新图片必须在以下三个方面与原图明显不同："
    "第一，改变拍摄场景，可调整时间、天气、光线、环境氛围或展示区域；"
    "第二，改变画面重点，选择原图中另一个真实存在的物品、设施或区域作为主体；"
    "第三，明显改变拍摄角度、距离和机位，例如左侧或右侧45度、近景、远景、"
    "高机位或低机位，避免复制原图构图。"
    "保持照片真实自然、透视合理、光影统一、细节清晰。"
    "如果无法确认某个物品或设施真实存在，则不要添加。"
    "不要添加无关人物、动物、错误文字、水印、边框或其他场所。"
)


def test_gateway_edit_matches_reference_request(tmp_path, monkeypatch):
    source = tmp_path / "source.png"
    Image.new("RGBA", (8, 6), (10, 20, 30, 128)).save(source)
    sent = {}

    class Response:
        ok = True
        status_code = 200
        content = b"{}"

        @staticmethod
        def json():
            return {"data": [{"url": "https://example.test/output.png"}]}

    def fake_post(url, **kwargs):
        sent.update(url=url, **kwargs)
        return Response()

    monkeypatch.setattr(image_gen, "_GATEWAY_BASE", "https://neolink.com/api/v1")
    monkeypatch.setattr(image_gen, "_gateway_key", lambda: "secret")
    monkeypatch.setattr(image_gen.requests, "post", fake_post)
    monkeypatch.setattr(image_gen, "record_llm_call", lambda **_kwargs: None)

    assert image_gen._gateway_edit(source) == "https://example.test/output.png"
    assert sent["url"] == "https://neolink.com/api/v1/images/edits"
    assert sent["headers"] == {"Authorization": "Bearer secret"}
    assert sent["data"] == {
        "model": "wan2.7-image",
        "prompt": EXPECTED_PROMPT,
        "output_format": "png",
        "size": "1536x1024",
    }
    field, (filename, image_file, mime) = sent["files"][0]
    assert (field, filename, mime) == ("image[]", "input.jpg", "image/jpeg")
    with Image.open(BytesIO(image_file.getvalue())) as converted:
        assert converted.format == "JPEG"
        assert converted.mode == "RGB"


def test_edit_prompt_appends_article_subject():
    assert image_gen.build_edit_prompt("") == EXPECTED_PROMPT
    assert image_gen.build_edit_prompt("   ") == EXPECTED_PROMPT

    prompt = image_gen.build_edit_prompt("西湖夜游攻略\n——晚上怎么逛最舒服")

    assert prompt.startswith(EXPECTED_PROMPT)
    assert "「西湖夜游攻略 ——晚上怎么逛最舒服」" in prompt
    assert len(image_gen.build_edit_prompt("长" * 1000)) < len(EXPECTED_PROMPT) + 300


def test_generate_passes_subject_into_prompt(tmp_path, monkeypatch):
    source_path = tmp_path / "source.png"
    source_path.write_bytes(b"source")
    prompts = []

    def fake_edit(_path, prompt):
        prompts.append(prompt)
        raise RuntimeError("stop after prompt")

    monkeypatch.setattr(image_gen, "_use_gateway", lambda: True)
    monkeypatch.setattr(image_gen, "_gateway_edit", fake_edit)
    from geo.services import media_storage
    monkeypatch.setattr(media_storage, "media_file", lambda _media: source_path)

    assert image_gen.generate_article_image(
        None, topic_id=3, user_id=5,
        source_media=SimpleNamespace(id=7, filename="source.png"),
        subject="亲子游攻略",
    ) is None
    assert prompts and "「亲子游攻略」" in prompts[0]


def test_marking_failure_deletes_local_and_object_storage(tmp_path, monkeypatch):
    source_path = tmp_path / "source.png"
    generated_path = tmp_path / "generated.png"
    source_path.write_bytes(b"source")
    generated_path.write_bytes(b"generated")
    deleted = []

    class FakeDb:
        def add(self, row):
            row.id = 99

        def commit(self):
            pass

        def refresh(self, _row):
            pass

        def delete(self, _row):
            pass

    monkeypatch.setattr(image_gen, "_use_gateway", lambda: True)
    monkeypatch.setattr(image_gen, "_gateway_edit", lambda _path, _prompt="": "https://example.test/a.png")
    monkeypatch.setattr(
        image_gen, "_download_to_media",
        lambda _url, _topic_id: (generated_path, len(b"generated"), ".png"),
    )
    monkeypatch.setattr(image_gen.image_marking, "stamp_media", lambda *_args, **_kwargs: False)

    from geo.services import media_storage
    monkeypatch.setattr(media_storage, "media_file", lambda _media: source_path)
    monkeypatch.setattr(
        media_storage, "delete_file",
        lambda topic_id, path: deleted.append((topic_id, path)),
    )

    source = SimpleNamespace(id=7, filename="source.png")
    assert image_gen.generate_article_image(
        FakeDb(), topic_id=3, user_id=5, source_media=source,
    ) is None
    assert deleted == [(3, str(generated_path))]


def test_text_image_prompt_uses_title_and_summary():
    prompt = image_gen.build_text_prompt("西湖亲子游攻略", "带娃  怎么玩\n最省心", "酒店")

    assert "「西湖亲子游攻略」" in prompt
    assert "带娃 怎么玩 最省心" in prompt
    assert "酒店" in prompt
    assert "插画" in prompt


def test_text_image_calls_generations_endpoint(monkeypatch):
    sent = {}

    class Response:
        ok = True
        status_code = 200
        content = b"{}"

        @staticmethod
        def json():
            return {"data": [{"url": "https://example.test/t2i.png"}]}

    def fake_post(url, **kwargs):
        sent.update(url=url, **kwargs)
        return Response()

    monkeypatch.setattr(image_gen, "_GATEWAY_BASE", "https://neolink.com/api/v1")
    monkeypatch.setattr(image_gen, "_gateway_key", lambda: "secret")
    monkeypatch.setattr(image_gen.requests, "post", fake_post)
    monkeypatch.setattr(image_gen, "record_llm_call", lambda **_kwargs: None)

    assert image_gen._gateway_generate("画一张图") == "https://example.test/t2i.png"
    assert sent["url"] == "https://neolink.com/api/v1/images/generations"
    assert sent["json"] == {"model": "wan2.7-image", "prompt": "画一张图",
                            "size": "1536x1024", "n": 1}


def test_text_image_without_title_or_gateway_is_skipped(monkeypatch):
    monkeypatch.setattr(image_gen, "_use_gateway", lambda: False)
    assert image_gen.generate_text_image(None, topic_id=1, user_id=1, title="标题") is None
    monkeypatch.setattr(image_gen, "_use_gateway", lambda: True)
    assert image_gen.generate_text_image(None, topic_id=1, user_id=1, title="  ") is None
