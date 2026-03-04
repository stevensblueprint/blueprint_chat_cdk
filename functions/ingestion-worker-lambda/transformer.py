def _rich_text_to_str(rich_texts: list) -> str:
    return "".join(rt.get("plain_text", "") for rt in rich_texts)


def notion_blocks_to_markdown(blocks: list) -> str:
    lines = []
    for block in blocks:
        btype = block.get("type")
        data = block.get(btype, {})
        rich_text = data.get("rich_text", [])
        text = _rich_text_to_str(rich_text)

        if btype == "paragraph":
            lines.append(text)
        elif btype == "heading_1":
            lines.append(f"# {text}")
        elif btype == "heading_2":
            lines.append(f"## {text}")
        elif btype == "heading_3":
            lines.append(f"### {text}")
        elif btype == "bulleted_list_item":
            lines.append(f"- {text}")
        elif btype == "numbered_list_item":
            lines.append(f"1. {text}")
        elif btype == "quote":
            lines.append(f"> {text}")
        elif btype == "callout":
            lines.append(f"> {text}")
        elif btype == "divider":
            lines.append("---")
        elif btype == "code":
            lang = data.get("language", "")
            lines.append(f"```{lang}\n{text}\n```")
        # unsupported block types are silently skipped

    return "\n\n".join(line for line in lines)
