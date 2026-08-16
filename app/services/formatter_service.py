import logging
from pathlib import Path
from typing import List, Optional

from app.core.config import settings
from app.core.schemas import TranscriptionResult, TranscriptionSegment

logger = logging.getLogger(__name__)


class SubtitleFormatter:
    def _format_timestamp(self, seconds: float, fmt_type: str = "srt") -> str:
        """
        Converts seconds to timestamp string.
        SRT: HH:MM:SS,mmm
        VTT: HH:MM:SS.mmm
        ASS: H:MM:SS.cc (centiseconds)
        """
        hrs = int(seconds // 3600)
        mins = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        msecs = int((seconds % 1) * 1000)

        if fmt_type == "srt":
            return f"{hrs:02}:{mins:02}:{secs:02},{msecs:03}"
        elif fmt_type == "vtt":
            return f"{hrs:02}:{mins:02}:{secs:02}.{msecs:03}"
        elif fmt_type == "ass":
            # ASS uses centiseconds (0-99)
            return f"{hrs}:{mins:02}:{secs:02}.{int(msecs / 10):02}"
        return ""

    def _collect_texts(self, index: int, result: TranscriptionResult, original_result: Optional[TranscriptionResult], extra_results: Optional[List[TranscriptionResult]]) -> List[str]:
        """Collect texts from original, main result, and extra translations for a segment index (1-based)."""
        texts = []
        if original_result and index <= len(original_result.segments):
            texts.append(original_result.segments[index - 1].text)
        texts.append(result.segments[index - 1].text)
        if extra_results:
            for er in extra_results:
                if index <= len(er.segments):
                    texts.append(er.segments[index - 1].text)
        return texts

    def to_srt(self, result: TranscriptionResult, original_result: Optional[TranscriptionResult] = None, extra_results: Optional[List[TranscriptionResult]] = None) -> str:
        """Converts TranscriptionResult to SRT format. Supports bilingual/multilingual."""
        lines = []
        for i, seg in enumerate(result.segments, 1):
            lines.append(str(i))
            lines.append(
                f"{self._format_timestamp(seg.start, 'srt')} --> {self._format_timestamp(seg.end, 'srt')}"
            )
            texts = self._collect_texts(i, result, original_result, extra_results)
            lines.append("\n".join(texts))
            lines.append("")  # Blank line between entries
        return "\n".join(lines)

    def to_vtt(self, result: TranscriptionResult, original_result: Optional[TranscriptionResult] = None, extra_results: Optional[List[TranscriptionResult]] = None) -> str:
        """Converts TranscriptionResult to WebVTT format. Supports bilingual/multilingual."""
        lines = ["WEBVTT\n"]
        for i, seg in enumerate(result.segments):
            lines.append(
                f"{self._format_timestamp(seg.start, 'vtt')} --> {self._format_timestamp(seg.end, 'vtt')}"
            )
            texts = self._collect_texts(i + 1, result, original_result, extra_results)
            lines.append("\n".join(texts))
            lines.append("")
        return "\n".join(lines)

    def _wrap_text(self, text: str, font_size: int = 20, target_width: int = 576) -> str:
        """Wrap text by inserting ASS line breaks \\N based on estimated pixel width."""
        text = text.replace('\r\n', ' ').replace('\n', ' ').replace('\r', ' ')
        text = ' '.join(text.split())
        if not text:
            return text

        def get_char_width(c: str) -> float:
            if '\u4e00' <= c <= '\u9fff' or '\u3400' <= c <= '\u4dbf' or '\uf900' <= c <= '\ufaff':
                return 1.0  # CJK
            if '\u3040' <= c <= '\u309f' or '\u30a0' <= c <= '\u30ff' or '\u31f0' <= c <= '\u31ff':
                return 0.95  # Hiragana/Katakana are almost as wide as CJK
            if '\uac00' <= c <= '\ud7af' or '\u1100' <= c <= '\u11ff' or '\u3130' <= c <= '\u318f':
                return 0.95  # Korean Hangul / Jamo
            if '\u3000' <= c <= '\u303f' or '\uff00' <= c <= '\uffef':
                return 0.8  # Fullwidth punctuation
            if c.isupper():
                return 0.55
            if c.islower():
                return 0.42
            if c.isdigit():
                return 0.48
            if c.isspace():
                return 0.3
            return 0.45  # Other punctuation/symbols

        # Use 90% of the target width as the wrap threshold to leave a safety margin
        # against font metric differences between our heuristic and the renderer.
        max_line_width = target_width * 0.9
        min_line_width = target_width * 0.3
        lines = []
        current_chars = []
        current_width = 0.0
        break_positions = []  # list of (pos, width) for spaces and CJK/Hiragana/Katakana/Hangul chars

        for c in text:
            w = get_char_width(c) * font_size
            if current_width + w > max_line_width and current_chars:
                chosen_pos = -1
                for pos, bw in reversed(break_positions):
                    remaining_chars = len(current_chars) - pos
                    preceding_width = current_width - (current_width - bw)  # bw includes break char width
                    preceding_width = bw
                    if remaining_chars >= 3 and preceding_width >= min_line_width:
                        chosen_pos = pos
                        break

                if chosen_pos > 0:
                    lines.append("".join(current_chars[:chosen_pos]).rstrip())
                    remaining = []
                    for ch in current_chars[chosen_pos:]:
                        if not remaining and ch.isspace():
                            continue
                        remaining.append(ch)
                    current_chars = remaining
                    current_width = sum(get_char_width(ch) * font_size for ch in remaining)
                else:
                    lines.append("".join(current_chars))
                    current_chars = []
                    current_width = 0.0

                break_positions = []

            current_chars.append(c)
            current_width += w

            if c.isspace() or ('\u4e00' <= c <= '\u9fff' or '\u3400' <= c <= '\u4dbf' or '\uf900' <= c <= '\ufaff' or
                              '\u3040' <= c <= '\u309f' or '\u30a0' <= c <= '\u30ff' or
                              '\uac00' <= c <= '\ud7af'):
                break_positions.append((len(current_chars), current_width))

        if current_chars:
            lines.append("".join(current_chars).rstrip())

        return r"\N".join(lines)

    def _hex_to_ass_color(self, hex_color: Optional[str]) -> Optional[str]:
        """Convert #RRGGBB to ASS &H00BBGGRR format."""
        if not hex_color or not hex_color.startswith('#') or len(hex_color) != 7:
            return None
        r = hex_color[1:3]
        g = hex_color[3:5]
        b = hex_color[5:7]
        return f"&H00{b}{g}{r}"

    def to_ass(self, result: TranscriptionResult, original_result: Optional[TranscriptionResult] = None, extra_results: Optional[List[TranscriptionResult]] = None, source_color: Optional[str] = None, target_color: Optional[str] = None, font_size: Optional[int] = None, offset_y: Optional[int] = None, show_source: bool = True, show_target: bool = True, playres_x: Optional[int] = None, playres_y: Optional[int] = None, bold: Optional[bool] = None, font_family: Optional[str] = None) -> str:
        """Converts TranscriptionResult to ASS format with simple styles. Supports bilingual/multilingual."""
        # ASS Header
        s = settings
        orig_color = self._hex_to_ass_color(source_color) or "&H00FFFFFF"
        trans_color = self._hex_to_ass_color(target_color) or "&H00FDE047"
        margin_v = offset_y or 0

        # Use provided resolution or fall back to settings defaults
        if playres_x is None:
            playres_x = getattr(s, 'ASS_PLAYRES_X', 1920)
        if playres_y is None:
            playres_y = getattr(s, 'ASS_PLAYRES_Y', 1080)
        # Scale font sizes proportionally to PlayResX (base = 640)
        scale = playres_x / 640.0
        orig_font = max(10, int((font_size or getattr(s, 'ASS_FONT_SIZE_ORIGINAL', 20)) * scale))
        trans_font = max(10, int((font_size or getattr(s, 'ASS_FONT_SIZE_TRANSLATED', 18)) * scale))
        margin_lr = max(10, int(playres_x * 0.05))
        target_width = playres_x - 2 * margin_lr
        bold_val = 1 if bold else 0
        font_name = font_family or getattr(s, 'ASS_FONT_NAME', 'Arial')
        logger.info(f"to_ass params: PlayResX={playres_x}, PlayResY={playres_y}, scale={scale:.2f}, margin_lr={margin_lr}, orig_font={orig_font}, trans_font={trans_font}, target_width={target_width}, bold={bold_val}, font={font_name}, font_size_input={font_size}")

        # Match the video preview layout: both subtitle blocks are anchored to
        # the bottom of the video, source above target. Use \pos to avoid libass
        # collision handling reordering the lines.
        center_x = playres_x // 2
        offset_px = margin_v
        bottom_y = playres_y - 80 - offset_px
        line_gap = 10

        lines = [
            "[Script Info]",
            "ScriptType: v4.00+",
            f"PlayResX: {playres_x}",
            f"PlayResY: {playres_y}",
            "ScaledBorderAndShadow: yes",
            "WrapStyle: 0",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
            f"Style: Original,{font_name},{orig_font},{orig_color},&H00000000,&H00000000,&H00000000,{bold_val},0,0,0,100,100,0,0,1,1,0,2,{margin_lr},{margin_lr},10,1",
            f"Style: Translated,{font_name},{trans_font},{trans_color},&H00000000,&H00000000,&H00000000,{bold_val},0,0,0,100,100,0,0,1,1,0,2,{margin_lr},{margin_lr},10,1",
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]

        for i, seg in enumerate(result.segments):
            start = self._format_timestamp(seg.start, "ass")
            end = self._format_timestamp(seg.end, "ass")
            # Translate is rendered first (bottom block), then source sits above it.
            trans_wrapped = None
            trans_lines_count = 1
            if show_target:
                texts = self._collect_texts(i + 1, result, original_result if show_source else None, extra_results)
                target_texts = texts[1:] if len(texts) > 1 else texts[:1]
                for text in target_texts:
                    trans_wrapped = self._wrap_text(text, font_size=trans_font, target_width=target_width)
                    trans_lines_count = trans_wrapped.count(r"\N") + 1
                    positioned = f"{{\\an2\\pos({center_x},{bottom_y})}}{trans_wrapped}"
                    lines.append(f"Dialogue: 0,{start},{end},Translated,,0,0,0,,{positioned}")
                    preview = trans_wrapped[:120].replace("\n", " ")
                    logger.info(f"_wrap_text target seg {i}: {preview}...")
            if show_source and original_result and i < len(original_result.segments):
                wrapped = self._wrap_text(original_result.segments[i].text, font_size=orig_font, target_width=target_width)
                if show_source and show_target and trans_wrapped is not None:
                    trans_height = trans_lines_count * trans_font
                    orig_y = bottom_y - trans_height - line_gap
                    positioned = f"{{\\an2\\pos({center_x},{orig_y})}}{wrapped}"
                else:
                    positioned = f"{{\\an2\\pos({center_x},{bottom_y})}}{wrapped}"
                lines.append(f"Dialogue: 0,{start},{end},Original,,0,0,0,,{positioned}")
                preview = wrapped[:120].replace("\n", " ")
                logger.info(f"_wrap_text source seg {i}: {preview}...")

        dialogue_lines = [l for l in lines if l.startswith("Dialogue:")]
        logger.info(f"to_ass generated {len(dialogue_lines)} dialogue lines. bottom_y={bottom_y}")
        for dl in dialogue_lines[:6]:
            logger.info(f"  {dl}")

        return "\n".join(lines)

    def save_to_file(self, content: str, filename: str, extension: str) -> Path:
        """Saves content to a file in the output directory."""
        output_path = settings.OUTPUT_DIR / f"{filename}.{extension}"
        settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w", encoding="utf-8-sig") as f:
            f.write(content)

        return output_path


subtitle_formatter = SubtitleFormatter()
