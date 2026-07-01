from cheapclaw.utils.text.rendered_code_blocks import restore_rendered_code_blocks


def test_restore_rendered_code_blocks_reconstructs_markdown_fence():
    rendered = "Here is code:\npython\n1\n2\nimport os\nprint(os.name)\nDone"

    restored = restore_rendered_code_blocks(rendered)

    assert restored == "Here is code:\n```python\nimport os\nprint(os.name)\n```\nDone"
