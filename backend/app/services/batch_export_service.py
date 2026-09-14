"""
批量导出服务模块

支持将已入库的视频转写和 AI 整理内容导出为多种格式：
- Markdown (.md)
- Word (.docx)
- Excel (.xlsx)
- PowerPoint (.pptx)
- PDF (.pdf)
支持合并为单个文件或打包为 ZIP。
"""
from __future__ import annotations

import io
import logging
import os
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional

ProgressCb = Optional[Callable[[int, int, str], None]]

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.entities import FavoriteVideo, VideoCache
from app.services.export_common import display_author, display_link
from app.services.markdown_export import export_ai_organized, export_original

logger = logging.getLogger(__name__)


class BatchExportService:
    """批量导出服务"""

    def get_exportable_videos(
        self,
        db: Session,
        collection_id: Optional[str] = None,
        selected_ids: Optional[List[str]] = None,
    ) -> List[tuple[VideoCache, Optional[FavoriteVideo]]]:
        """
        获取符合导出条件的视频列表（已入库 done 状态）
        """
        # `VideoCache`/`FavoriteVideo` are compat aliases for `IngestionItem`/
        # `ContentItem` (see entities.py). `VideoCache.platform_item_id` is a
        # hybrid_property routed through the `content_item` relationship, so
        # without eager loading, every access below was a lazy-load query per
        # row -- on top of the separate FavoriteVideo query per row. Eager
        # load the relationship and reuse it directly as `fv` (they are the
        # same row via the FK, not a lookup by the non-unique-across-platforms
        # platform_item_id string, which could collide between platforms).
        query = (
            select(VideoCache)
            .options(selectinload(VideoCache.content_item))
            .where(VideoCache.status == "done")
        )

        if selected_ids:
            query = query.join(VideoCache.content_item).where(
                FavoriteVideo.remote_item_id.in_(selected_ids)
            )

        caches = db.execute(query).scalars().all()

        results = []
        for cache in caches:
            fv = cache.content_item

            if collection_id and collection_id != "all":
                if not fv or str(fv.collection_id) != str(collection_id):
                    continue

            results.append((cache, fv))

        return results

    def _prewarm_ai_summaries(
        self, db, items: list, content_type: str, progress_cb: ProgressCb
    ) -> None:
        """
        导出前把每条的 AI 整理算好并缓存进 VideoCache.summary，进度回调也在这里推进。
        之后各 _export_* 里的 export_ai_organized(cache, generate=False) 会直接命中缓存。
        """
        if content_type not in ("ai", "both"):
            if progress_cb:
                progress_cb(len(items), len(items), "正在生成导出文件...")
            return
        total = len(items)
        for i, (cache, _fv) in enumerate(items, 1):
            if progress_cb:
                progress_cb(i - 1, total, f"AI 整理：{(cache.title or '')[:16]}")
            try:
                export_ai_organized(cache, db=db)
            except Exception as exc:  # noqa: BLE001
                logger.warning("AI 整理失败 [%s]: %s", cache.platform_item_id, exc)
        if progress_cb:
            progress_cb(total, total, "正在生成导出文件...")

    def export_batch(
        self,
        db: Session,
        collection_id: Optional[str] = None,
        selected_ids: Optional[List[str]] = None,
        content_type: str = "both",  # "original" | "ai" | "both"
        export_format: str = "markdown",  # "markdown" | "word" | "excel" | "ppt" | "pdf"
        pack_mode: str = "single",  # "single" | "zip"
        progress_cb: ProgressCb = None,
    ) -> tuple[io.BytesIO, str, str]:
        """
        执行批量导出

        :return: (文件流 buffer, 文件名 filename, MIME 类型 mime_type)
        """
        items = self.get_exportable_videos(db, collection_id, selected_ids)
        if not items:
            raise ValueError("没有可导出的入库视频内容，请先执行入库")

        self._prewarm_ai_summaries(db, items, content_type, progress_cb)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        prefix = f"Akasha-RAG_Export_{timestamp}"

        if export_format == "excel":
            buffer = self._export_excel(items, content_type)
            return buffer, f"{prefix}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

        if export_format == "word":
            if pack_mode == "single":
                buffer = self._export_word_single(items, content_type)
                return buffer, f"{prefix}.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            else:
                buffer = self._export_word_zip(items, content_type)
                return buffer, f"{prefix}_Word.zip", "application/zip"

        if export_format == "ppt":
            buffer = self._export_ppt(items, content_type)
            return buffer, f"{prefix}.pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"

        if export_format == "pdf":
            buffer = self._export_pdf(items, content_type)
            return buffer, f"{prefix}.pdf", "application/pdf"

        # 默认为 markdown
        if pack_mode == "single":
            buffer = self._export_markdown_single(items, content_type)
            return buffer, f"{prefix}.md", "text/markdown; charset=utf-8"
        else:
            buffer = self._export_markdown_zip(items, content_type)
            return buffer, f"{prefix}_Markdown.zip", "application/zip"

    def export_to_local_directory(
        self,
        db: Session,
        collection_id: Optional[str] = None,
        selected_ids: Optional[List[str]] = None,
        content_type: str = "both",
        export_format: str = "markdown",
        pack_mode: str = "single",
        target_dir: str = "",
        auto_open: bool = True,
        progress_cb: ProgressCb = None,
    ) -> dict:
        """
        导出并直接保存至本地自定义文件夹路径

        若 pack_mode == "single"：直接保存为一个完整的合并文件
        若 pack_mode == "zip" / "multiple"：直接在该目录下生成各篇独立的解包文件（无需手动二次解压）
        """
        import subprocess

        if not target_dir or not target_dir.strip():
            target_dir = str(Path.cwd() / "exports")

        target_path = Path(target_dir.strip())
        target_path.mkdir(parents=True, exist_ok=True)

        items = self.get_exportable_videos(db, collection_id, selected_ids)
        if not items:
            raise ValueError("没有可导出的入库视频内容，请先执行入库")

        written_files = []

        if pack_mode == "single":
            # export_batch() below does its own prewarm; calling it here too
            # would just re-run the same per-item loop (and double-fire
            # progress_cb) since the summaries are already cached by then.
            buffer, filename, _ = self.export_batch(
                db, collection_id, selected_ids, content_type, export_format, "single", progress_cb=progress_cb
            )
            out_file = target_path / filename
            with open(out_file, "wb") as f:
                f.write(buffer.getvalue())
            written_files.append(str(out_file.name))
        else:
            # AI 整理预热 + 进度推进（之后各 _export_* 命中缓存）——多文件模式
            # 不经过 export_batch()，预热只能在这里做一次。
            self._prewarm_ai_summaries(db, items, content_type, progress_cb)
            # 多文件模式：直接将各篇文件分别写入目标目录
            if export_format == "markdown":
                for idx, (cache, fv) in enumerate(items, 1):
                    md_str = self._render_item_markdown(cache, fv, content_type)
                    safe_title = "".join(c for c in cache.title if c not in r'\/:*?"<>|').strip()[:40] or f"video_{cache.platform_item_id}"
                    fname = f"{idx:03d}_{safe_title}.md"
                    out_file = target_path / fname
                    with open(out_file, "w", encoding="utf-8") as f:
                        f.write(md_str)
                    written_files.append(fname)
            elif export_format == "word":
                for idx, (cache, fv) in enumerate(items, 1):
                    doc_buf = self._export_word_single([(cache, fv)], content_type)
                    safe_title = "".join(c for c in cache.title if c not in r'\/:*?"<>|').strip()[:40] or f"video_{cache.platform_item_id}"
                    fname = f"{idx:03d}_{safe_title}.docx"
                    out_file = target_path / fname
                    with open(out_file, "wb") as f:
                        f.write(doc_buf.getvalue())
                    written_files.append(fname)
            elif export_format == "pdf":
                for idx, (cache, fv) in enumerate(items, 1):
                    pdf_buf = self._export_pdf([(cache, fv)], content_type)
                    safe_title = "".join(c for c in cache.title if c not in r'\/:*?"<>|').strip()[:40] or f"video_{cache.platform_item_id}"
                    fname = f"{idx:03d}_{safe_title}.pdf"
                    out_file = target_path / fname
                    with open(out_file, "wb") as f:
                        f.write(pdf_buf.getvalue())
                    written_files.append(fname)
            else:
                # Excel / PPT 等导出为单个结构化文件
                buffer, filename, _ = self.export_batch(
                    db, collection_id, selected_ids, content_type, export_format, "single"
                )
                out_file = target_path / filename
                with open(out_file, "wb") as f:
                    f.write(buffer.getvalue())
                written_files.append(str(out_file.name))

        abs_dir = str(target_path.resolve())
        if auto_open:
            try:
                if sys.platform == "win32":
                    os.startfile(abs_dir)
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", abs_dir])
                else:
                    subprocess.Popen(["xdg-open", abs_dir])
            except Exception:
                pass

        return {
            "success": True,
            "target_dir": abs_dir,
            "file_count": len(written_files),
            "files": written_files[:10],
            "message": f"成功导出 {len(written_files)} 个文件至 {abs_dir}",
        }

    # -------------------------------------------------------------
    # Markdown 导出
    # -------------------------------------------------------------
    def _render_item_markdown(self, cache: VideoCache, fv: Optional[FavoriteVideo], content_type: str) -> str:
        author = display_author(fv)
        lines = [
            f"# {cache.title}",
            "",
            f"- **视频作者**：{author}",
            f"- **视频链接**：{display_link(fv)}",
            f"- **导出时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
        ]

        if content_type in ("ai", "both"):
            try:
                ai_text = export_ai_organized(cache, generate=False)
                lines.extend(["## 💡 AI 结构化整理", "", ai_text, ""])
            except Exception as e:
                logger.warning("生成 AI 整理失败: %s", e)
                lines.extend(["## 💡 AI 结构化整理", "", "（暂未生成或处理异常）", ""])

        if content_type in ("original", "both"):
            orig = (cache.transcript_text or "").strip()
            lines.extend(["## 📝 原始转写全文", "", orig if orig else "（暂无转写文本）", ""])

        lines.append("\n---\n")
        return "\n".join(lines)

    def _export_markdown_single(self, items: list, content_type: str) -> io.BytesIO:
        content_parts = [
            "# Akasha-RAG 收藏夹知识库导出",
            f"> 导出总数：{len(items)} 条 | 导出日期：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "---",
            "",
        ]
        for cache, fv in items:
            content_parts.append(self._render_item_markdown(cache, fv, content_type))

        full_text = "\n".join(content_parts)
        buf = io.BytesIO(full_text.encode("utf-8"))
        buf.seek(0)
        return buf

    def _export_markdown_zip(self, items: list, content_type: str) -> io.BytesIO:
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for idx, (cache, fv) in enumerate(items, 1):
                md_str = self._render_item_markdown(cache, fv, content_type)
                safe_title = "".join(c for c in cache.title if c not in r'\/:*?"<>|').strip()[:40] or f"video_{cache.platform_item_id}"
                filename = f"{idx:03d}_{safe_title}.md"
                zf.writestr(filename, md_str.encode("utf-8"))
        zip_buf.seek(0)
        return zip_buf

    # -------------------------------------------------------------
    # Excel 导出 (.xlsx)
    # -------------------------------------------------------------
    @staticmethod
    def _chunk_for_excel_cell(text: str, limit: int = 30000) -> list[str]:
        """Split text into pieces that each fit under Excel's ~32767-char
        per-cell limit, leaving headroom below the hard limit.

        Returns `[""]` for empty input so callers always have at least one
        chunk to put in the main row.
        """
        if not text:
            return [""]
        return [text[i : i + limit] for i in range(0, len(text), limit)]

    def _export_excel(self, items: list, content_type: str) -> io.BytesIO:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill

        wb = Workbook()
        ws = wb.active
        ws.title = "抖音知识库导出"

        headers = ["序号", "视频ID", "视频标题", "作者", "视频链接", "更新时间"]
        if content_type in ("ai", "both"):
            headers.append("AI 结构化整理")
        if content_type in ("original", "both"):
            headers.append("原始转写文本")

        ws.append(headers)

        header_fill = PatternFill(start_color="F26D5B", end_color="F26D5B", fill_type="solid")
        header_font = Font(name="微软雅黑", size=11, bold=True, color="FFFFFF")
        for col_num in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col_num)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center")

        align_center = Alignment(horizontal="center", vertical="top")
        align_left = Alignment(horizontal="left", vertical="top", wrap_text=True)
        font_body = Font(name="微软雅黑", size=10)

        for idx, (cache, fv) in enumerate(items, 1):
            author = display_author(fv)
            url = display_link(fv)
            time_str = cache.updated_at.strftime("%Y-%m-%d %H:%M") if cache.updated_at else ""

            row_data = [idx, cache.platform_item_id, cache.title, author, url, time_str]
            # 长文本列（AI 整理 / 原始转写）如果超过单元格上限，按 chunk 拆
            # 成多段——主行放第一段，需要更多段的追加"延续行"，不静默截断、
            # 不丢内容，也不引入新列或依赖打包模式。
            long_text_chunks: list[list[str]] = []

            if content_type in ("ai", "both"):
                try:
                    ai_text = export_ai_organized(cache, generate=False)
                except Exception:
                    ai_text = ""
                chunks = self._chunk_for_excel_cell(ai_text)
                row_data.append(chunks[0])
                long_text_chunks.append(chunks)

            if content_type in ("original", "both"):
                chunks = self._chunk_for_excel_cell(cache.transcript_text or "")
                row_data.append(chunks[0])
                long_text_chunks.append(chunks)

            long_text_col_start = len(row_data) - len(long_text_chunks)

            ws.append(row_data)
            current_row = ws.max_row
            for col_num in range(1, len(row_data) + 1):
                c = ws.cell(row=current_row, column=col_num)
                c.font = font_body
                if col_num in (1, 2, 6):
                    c.alignment = align_center
                else:
                    c.alignment = align_left

            max_chunks = max((len(c) for c in long_text_chunks), default=1)
            for chunk_idx in range(1, max_chunks):
                continuation = [""] * len(row_data)
                continuation[1] = f"{cache.platform_item_id}（续）"
                for offset, chunks in enumerate(long_text_chunks):
                    if chunk_idx < len(chunks):
                        continuation[long_text_col_start + offset] = chunks[chunk_idx]
                ws.append(continuation)
                cont_row = ws.max_row
                for col_num in range(1, len(continuation) + 1):
                    c = ws.cell(row=cont_row, column=col_num)
                    c.font = font_body
                    c.alignment = align_center if col_num in (1, 2, 6) else align_left

        ws.column_dimensions["A"].width = 8
        ws.column_dimensions["B"].width = 22
        ws.column_dimensions["C"].width = 32
        ws.column_dimensions["D"].width = 18
        ws.column_dimensions["E"].width = 38
        ws.column_dimensions["F"].width = 18
        if "AI 结构化整理" in headers:
            col_letter = chr(ord("A") + headers.index("AI 结构化整理"))
            ws.column_dimensions[col_letter].width = 50
        if "原始转写文本" in headers:
            col_letter = chr(ord("A") + headers.index("原始转写文本"))
            ws.column_dimensions[col_letter].width = 60

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return buf

    # -------------------------------------------------------------
    # Word 导出 (.docx)
    # -------------------------------------------------------------
    def _export_word_single(self, items: list, content_type: str) -> io.BytesIO:
        from docx import Document

        doc = Document()

        title = doc.add_heading("Akasha-RAG 知识库内容汇编", level=0)
        title.alignment = 1

        sub = doc.add_paragraph(f"导出收录视频数：{len(items)} 条  |  导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        sub.alignment = 1
        doc.add_paragraph()

        for idx, (cache, fv) in enumerate(items, 1):
            author = display_author(fv)
            doc.add_heading(f"{idx}. {cache.title}", level=1)

            table = doc.add_table(rows=2, cols=2)
            table.style = "Table Grid"
            table.cell(0, 0).text = f"作者: {author}"
            table.cell(0, 1).text = f"视频ID: {cache.platform_item_id}"
            table.cell(1, 0).text = f"链接: {display_link(fv)}"
            table.cell(1, 1).text = f"导出时间: {datetime.now().strftime('%Y-%m-%d')}"

            doc.add_paragraph()

            if content_type in ("ai", "both"):
                doc.add_heading("AI 结构化整理", level=2)
                try:
                    ai_text = export_ai_organized(cache, generate=False)
                except Exception:
                    ai_text = "（暂无 AI 整理内容）"
                for p_text in ai_text.split("\n\n"):
                    if p_text.strip():
                        doc.add_paragraph(p_text.strip())

            if content_type in ("original", "both"):
                doc.add_heading("原始转写全文", level=2)
                raw_text = (cache.transcript_text or "（暂无转写文本）").strip()
                for p_text in raw_text.split("\n\n"):
                    if p_text.strip():
                        doc.add_paragraph(p_text.strip())

            doc.add_page_break()

        buf = io.BytesIO()
        doc.save(buf)
        buf.seek(0)
        return buf

    def _export_word_zip(self, items: list, content_type: str) -> io.BytesIO:
        from docx import Document

        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for idx, (cache, fv) in enumerate(items, 1):
                doc = Document()
                author = display_author(fv)
                doc.add_heading(cache.title, level=0)

                doc.add_paragraph(f"作者: {author}  |  链接: {display_link(fv)}")
                doc.add_paragraph("---")

                if content_type in ("ai", "both"):
                    doc.add_heading("AI 结构化整理", level=1)
                    try:
                        ai_text = export_ai_organized(cache, generate=False)
                    except Exception:
                        ai_text = "（暂无 AI 整理内容）"
                    doc.add_paragraph(ai_text)

                if content_type in ("original", "both"):
                    doc.add_heading("原始转写全文", level=1)
                    doc.add_paragraph(cache.transcript_text or "（暂无转写文本）")

                doc_buf = io.BytesIO()
                doc.save(doc_buf)
                doc_buf.seek(0)

                safe_title = "".join(c for c in cache.title if c not in r'\/:*?"<>|').strip()[:40] or f"video_{cache.platform_item_id}"
                zf.writestr(f"{idx:03d}_{safe_title}.docx", doc_buf.getvalue())

        zip_buf.seek(0)
        return zip_buf

    # -------------------------------------------------------------
    # PowerPoint 导出 (.pptx)
    # -------------------------------------------------------------
    def _export_ppt(self, items: list, content_type: str) -> io.BytesIO:
        from pptx import Presentation
        from pptx.util import Inches, Pt

        prs = Presentation()
        prs.slide_width = Inches(13.333)
        prs.slide_height = Inches(7.5)

        blank_layout = prs.slide_layouts[6]
        cover_slide = prs.slides.add_slide(blank_layout)
        txBox = cover_slide.shapes.add_textbox(Inches(1.5), Inches(2.5), Inches(10.333), Inches(2.5))
        tf = txBox.text_frame
        p = tf.paragraphs[0]
        p.text = "Akasha-RAG 精选知识库汇编"
        p.font.size = Pt(40)
        p.font.bold = True

        p2 = tf.add_paragraph()
        p2.text = f"精选收录条目：{len(items)} 个  |  生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}"
        p2.font.size = Pt(18)

        for idx, (cache, fv) in enumerate(items, 1):
            author = display_author(fv)
            slide = prs.slides.add_slide(blank_layout)

            title_box = slide.shapes.add_textbox(Inches(0.8), Inches(0.5), Inches(11.7), Inches(1.0))
            tf_title = title_box.text_frame
            p_t = tf_title.paragraphs[0]
            p_t.text = f"#{idx} {cache.title}"
            p_t.font.size = Pt(22)
            p_t.font.bold = True

            p_sub = tf_title.add_paragraph()
            p_sub.text = f"作者: {author}  |  链接: {display_link(fv)}"
            p_sub.font.size = Pt(12)

            content_box = slide.shapes.add_textbox(Inches(0.8), Inches(1.8), Inches(11.7), Inches(5.0))
            tf_c = content_box.text_frame
            tf_c.word_wrap = True

            text_to_show = ""
            if content_type in ("ai", "both"):
                try:
                    text_to_show = export_ai_organized(cache, generate=False)
                except Exception:
                    text_to_show = ""

            if not text_to_show or content_type == "original":
                text_to_show = (cache.transcript_text or "").strip()

            lines = [line.strip() for line in text_to_show.split("\n") if line.strip()][:15]
            summary_preview = "\n".join(lines)
            if len(summary_preview) > 800:
                summary_preview = summary_preview[:800] + "..."

            p_body = tf_c.paragraphs[0]
            p_body.text = summary_preview if summary_preview else "暂无整理提纲"
            p_body.font.size = Pt(13)

        buf = io.BytesIO()
        prs.save(buf)
        buf.seek(0)
        return buf

    # -------------------------------------------------------------
    # PDF 导出 (.pdf)
    # -------------------------------------------------------------
    def _export_pdf(self, items: list, content_type: str) -> io.BytesIO:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer

        font_name = "Helvetica"
        font_paths = [
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/simsun.ttc",
            "C:/Windows/Fonts/simhei.ttf",
        ]
        for fp in font_paths:
            if os.path.exists(fp):
                try:
                    pdfmetrics.registerFont(TTFont("CustomChineseFont", fp))
                    font_name = "CustomChineseFont"
                    break
                except Exception:
                    pass

        buf = io.BytesIO()
        doc = SimpleDocTemplate(
            buf,
            pagesize=A4,
            rightMargin=40,
            leftMargin=40,
            topMargin=40,
            bottomMargin=40,
        )

        styles = getSampleStyleSheet()
        title_style = ParagraphStyle("DocTitle", parent=styles["Title"], fontName=font_name, fontSize=22, leading=26, alignment=1)
        h1_style = ParagraphStyle("H1", parent=styles["Heading1"], fontName=font_name, fontSize=15, leading=20)
        h2_style = ParagraphStyle("H2", parent=styles["Heading2"], fontName=font_name, fontSize=12, leading=16)
        body_style = ParagraphStyle("Body", parent=styles["BodyText"], fontName=font_name, fontSize=9, leading=14)
        meta_style = ParagraphStyle("Meta", parent=styles["Normal"], fontName=font_name, fontSize=8, leading=12, textColor="gray")

        story = [
            Spacer(1, 20),
            Paragraph("Akasha-RAG 知识库汇编报告", title_style),
            Spacer(1, 10),
            Paragraph(f"收录视频总数: {len(items)} 条 | 导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", meta_style),
            Spacer(1, 15),
            HRFlowable(width="100%", thickness=1, color="gray"),
            Spacer(1, 15),
        ]

        for idx, (cache, fv) in enumerate(items, 1):
            author = display_author(fv)
            story.append(Paragraph(f"{idx}. {cache.title}", h1_style))
            story.append(Paragraph(f"作者: {author}  |  链接: {display_link(fv)}", meta_style))
            story.append(Spacer(1, 8))

            if content_type in ("ai", "both"):
                story.append(Paragraph("AI 结构化整理", h2_style))
                try:
                    ai_text = export_ai_organized(cache, generate=False)
                except Exception:
                    ai_text = "暂无 AI 整理内容"
                for line in ai_text.split("\n"):
                    if line.strip():
                        story.append(Paragraph(line.strip(), body_style))
                        story.append(Spacer(1, 3))
                story.append(Spacer(1, 8))

            if content_type in ("original", "both"):
                story.append(Paragraph("原始转写全文", h2_style))
                orig = (cache.transcript_text or "").strip()
                for line in orig.split("\n"):
                    if line.strip():
                        story.append(Paragraph(line.strip(), body_style))
                        story.append(Spacer(1, 3))

            story.append(Spacer(1, 15))
            story.append(HRFlowable(width="100%", thickness=0.5, color="lightgrey"))
            story.append(Spacer(1, 15))

        doc.build(story)
        buf.seek(0)
        return buf


batch_export_service = BatchExportService()