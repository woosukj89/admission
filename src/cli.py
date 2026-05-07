"""CLI for Korean University Admission Crawler"""

import asyncio
import sys
from pathlib import Path
from typing import Optional

# Fix Windows console encoding for Korean characters
if sys.platform == "win32":
    import os
    os.system("")  # Enable ANSI escape codes on Windows
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import typer
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn

from .config import settings
from .utils.logging import setup_logging, get_logger

app = typer.Typer(
    name="admission",
    help="Korean University Admission Information Crawler",
    add_completion=False,
)
console = Console(force_terminal=True)
logger = get_logger("cli")


@app.callback()
def main(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose logging"),
    log_file: Optional[Path] = typer.Option(None, "--log-file", help="Log to file"),
):
    """Korean University Admission Crawler"""
    log_level = "DEBUG" if verbose else settings.log_level
    setup_logging(level=log_level, log_file=log_file)


# Universities commands
universities_app = typer.Typer(help="Manage university list")
app.add_typer(universities_app, name="universities")


@universities_app.command("update")
def universities_update():
    """Update the list of Korean universities"""
    from .universities.fetcher import UniversityFetcher

    async def _update():
        fetcher = UniversityFetcher()
        with console.status("[bold green]Updating university list..."):
            universities = await fetcher.update()
        console.print(f"[green]Updated: {len(universities)} universities")

    asyncio.run(_update())


@universities_app.command("list")
def universities_list(
    search: Optional[str] = typer.Option(None, "--search", "-s", help="Search by name"),
):
    """List all universities"""
    from .universities.fetcher import UniversityFetcher

    fetcher = UniversityFetcher()
    universities = fetcher.get_universities()

    if search:
        search_lower = search.lower()
        universities = [
            u for u in universities
            if search_lower in u.name.lower() or
               (u.name_en and search_lower in u.name_en.lower())
        ]

    table = Table(title="Korean Universities")
    table.add_column("Name", style="cyan")
    table.add_column("English Name", style="green")
    table.add_column("URL", style="blue")
    table.add_column("Location", style="yellow")

    for uni in universities[:50]:  # Limit display
        table.add_row(
            uni.name,
            uni.name_en or "-",
            uni.url[:50] + "..." if len(uni.url) > 50 else uni.url,
            uni.location or "-",
        )

    console.print(table)

    if len(universities) > 50:
        console.print(f"\n[dim]Showing 50 of {len(universities)} universities. Use --search to filter.[/dim]")


# Crawl commands
@app.command("crawl")
def crawl(
    university: Optional[str] = typer.Option(None, "--university", "-u", help="Crawl specific university"),
    workers: int = typer.Option(5, "--workers", "-w", help="Number of concurrent workers"),
    depth: int = typer.Option(3, "--depth", "-d", help="Maximum crawl depth"),
    resume: bool = typer.Option(False, "--resume", "-r", help="Resume previous crawl"),
):
    """Start crawling universities for admission information"""
    from .crawler.engine import CrawlerEngine
    from .universities.fetcher import UniversityFetcher

    settings.ensure_directories()

    async def _crawl():
        engine = CrawlerEngine(max_workers=workers, max_depth=depth)
        fetcher = UniversityFetcher()

        if resume:
            console.print("[yellow]Resuming previous crawl...[/yellow]")
            stats = await engine.resume(university)
        elif university:
            uni = fetcher.find_by_name(university)
            if not uni:
                console.print(f"[red]University not found: {university}[/red]")
                raise typer.Exit(1)

            console.print(f"[green]Crawling {uni.name}...[/green]")
            stats = {uni.name: await engine.crawl_university(uni)}
        else:
            universities = fetcher.get_universities()
            console.print(f"[green]Crawling {len(universities)} universities...[/green]")

            def progress_callback(uni_name: str, crawled: int, queued: int):
                console.print(f"  {uni_name}: {crawled} crawled, {queued} queued", end="\r")

            stats = await engine.crawl_all(universities, progress_callback)

        # Print summary
        console.print("\n[bold]Crawl Summary:[/bold]")
        table = Table()
        table.add_column("University")
        table.add_column("Pages", justify="right")
        table.add_column("Documents", justify="right")
        table.add_column("Admission", justify="right")
        table.add_column("Errors", justify="right")

        total_pages = 0
        total_docs = 0
        total_admission = 0
        total_errors = 0

        for name, uni_stats in stats.items():
            table.add_row(
                name,
                str(uni_stats.pages_crawled),
                str(uni_stats.documents_downloaded),
                str(uni_stats.admission_pages_found),
                str(uni_stats.errors),
            )
            total_pages += uni_stats.pages_crawled
            total_docs += uni_stats.documents_downloaded
            total_admission += uni_stats.admission_pages_found
            total_errors += uni_stats.errors

        table.add_row(
            "[bold]Total[/bold]",
            f"[bold]{total_pages}[/bold]",
            f"[bold]{total_docs}[/bold]",
            f"[bold]{total_admission}[/bold]",
            f"[bold]{total_errors}[/bold]",
        )

        console.print(table)

    asyncio.run(_crawl())


# Export commands
@app.command("export")
def export(
    output: Path = typer.Option(Path("./export"), "--output", "-o", help="Output directory"),
    format: str = typer.Option("json", "--format", "-f", help="Export format (json)"),
    university: Optional[str] = typer.Option(None, "--university", "-u", help="Export specific university"),
):
    """Export crawled data"""
    from .storage.json_storage import JSONStorage

    settings.ensure_directories()
    output.mkdir(parents=True, exist_ok=True)

    json_storage = JSONStorage()

    if university:
        export_file = json_storage.export_admission_pages(university)
        console.print(f"[green]Exported to: {export_file}[/green]")
    else:
        export_file = json_storage.export_all(output)
        console.print(f"[green]Exported all data to: {export_file}[/green]")


# Stats commands
@app.command("stats")
def stats(
    university: Optional[str] = typer.Option(None, "--university", "-u", help="Stats for specific university"),
):
    """Show crawl statistics"""
    from .storage.sqlite_storage import SQLiteStorage

    db = SQLiteStorage()

    if university:
        uni_stats = db.get_university_stats(university)
        console.print(f"\n[bold]Statistics for {university}:[/bold]")
        console.print(f"  Pages crawled: {uni_stats['pages']}")
        console.print(f"  Admission pages: {uni_stats['admission_pages']}")
        console.print(f"  Documents: {uni_stats['documents']}")
        console.print(f"  Queue status: {uni_stats['queue']}")
    else:
        overall_stats = db.get_stats()
        console.print("\n[bold]Overall Statistics:[/bold]")
        console.print(f"  Universities: {overall_stats['universities']}")
        console.print(f"  Total pages: {overall_stats['total_pages']}")
        console.print(f"  Admission pages: {overall_stats['admission_pages']}")
        console.print(f"  Total documents: {overall_stats['total_documents']}")
        console.print(f"  Admission documents: {overall_stats['admission_documents']}")
        console.print(f"  Queue status: {overall_stats['queue']}")


# Search commands
@app.command("search")
def search(
    query: str = typer.Argument(..., help="Search query"),
    university: Optional[str] = typer.Option(None, "--university", "-u", help="Filter by university"),
    admission_only: bool = typer.Option(True, "--admission-only/--all", help="Only admission-related pages"),
    limit: int = typer.Option(20, "--limit", "-l", help="Maximum results"),
):
    """Search crawled content"""
    from .storage.sqlite_storage import SQLiteStorage

    db = SQLiteStorage()
    results = db.search_pages(
        query=query,
        university=university,
        admission_only=admission_only,
        limit=limit,
    )

    if not results:
        console.print("[yellow]No results found.[/yellow]")
        return

    table = Table(title=f"Search Results for '{query}'")
    table.add_column("University", style="cyan")
    table.add_column("Title", style="green")
    table.add_column("Score", justify="right")
    table.add_column("URL", style="blue")

    for page in results:
        title = page["title"] or "[No title]"
        if len(title) > 40:
            title = title[:37] + "..."

        url = page["url"]
        if len(url) > 50:
            url = url[:47] + "..."

        table.add_row(
            page["university"],
            title,
            f"{page['admission_score']:.2f}",
            url,
        )

    console.print(table)


# Extract commands
@app.command("extract")
def extract(
    file_path: Path = typer.Argument(..., help="Path to document to extract"),
):
    """Extract text from a document (PDF, DOC, HWP)"""
    from .extractors.pdf_extractor import PDFExtractor
    from .extractors.doc_extractor import DocumentExtractor

    if not file_path.exists():
        console.print(f"[red]File not found: {file_path}[/red]")
        raise typer.Exit(1)

    ext = file_path.suffix.lower()

    if ext == ".pdf":
        extractor = PDFExtractor()
        result = extractor.extract(file_path)
    elif ext in (".doc", ".docx", ".hwp", ".hwpx"):
        extractor = DocumentExtractor()
        result = extractor.extract(file_path)
    else:
        console.print(f"[red]Unsupported file type: {ext}[/red]")
        raise typer.Exit(1)

    console.print(f"\n[bold]Extracted from {file_path.name}:[/bold]\n")
    console.print(result.get("text", "[No text extracted]"))

    if "metadata" in result:
        console.print(f"\n[bold]Metadata:[/bold]")
        for key, value in result["metadata"].items():
            console.print(f"  {key}: {value}")


# Recommend commands
@app.command("recommend")
def recommend(
    question: str = typer.Argument(..., help="자연어 질문 (e.g. '영문과 수능 수학 4등급 내신 3등급')"),
    limit: int = typer.Option(10, "--limit", "-l", help="최대 추천 수"),
    verbose: bool = typer.Option(False, "--verbose", help="상세 분석 출력"),
    admission_type: Optional[str] = typer.Option(None, "--type", "-t", help="수시 or 정시"),
):
    """AI 기반 입시 추천 (수능/내신 기반 대학·전형 추천)"""
    from rich.panel import Panel

    from .recommend.pipeline import RecommendationPipeline
    from .storage.admission_store import AdmissionStore

    # Check API key
    api_key = settings.anthropic_api_key
    if not api_key:
        console.print("[red]Anthropic API 키가 설정되지 않았습니다.[/red]")
        console.print()
        console.print("설정 방법:")
        console.print("  1. .env 파일에 추가: [cyan]CRAWLER_ANTHROPIC_API_KEY=sk-ant-...[/cyan]")
        console.print("  2. 환경변수 설정: [cyan]set CRAWLER_ANTHROPIC_API_KEY=sk-ant-...[/cyan]")
        raise typer.Exit(1)

    store = AdmissionStore()
    pipeline = RecommendationPipeline(
        store=store,
        api_key=api_key,
        model=settings.llm_model,
    )

    # Progress callback
    def on_stage(stage: int, name: str, detail: str):
        if detail:
            console.print(f"  [dim]{detail}[/dim]")

    try:
        with console.status("[bold green][1/3] 학생 프로필 분석 중...") as status:
            def progress_cb(stage, name, detail):
                labels = {1: "학생 프로필 분석", 2: "후보 전형 검색", 3: "AI 분석"}
                status.update(f"[bold green][{stage}/3] {name}...")
                on_stage(stage, name, detail)

            result = pipeline.run(
                question=question,
                admission_type=admission_type,
                limit=limit,
                on_stage=progress_cb,
            )
    except Exception as e:
        console.print(f"[red]오류 발생: {e}[/red]")
        raise typer.Exit(1)

    # Display profile summary
    profile = result.profile
    console.print()
    profile_parts = []
    if profile.target_department_keywords:
        profile_parts.append(f"목표: {', '.join(profile.target_department_keywords[:3])}")
    if profile.suneung_grades:
        grades = " ".join(f"{k}{v}" for k, v in profile.suneung_grades.items() if v is not None)
        if grades:
            profile_parts.append(f"수능: {grades}")
    if profile.gpa_grade is not None:
        profile_parts.append(f"내신: {profile.gpa_grade}등급")
    console.print(Panel(" | ".join(profile_parts), title="학생 프로필", border_style="cyan"))

    console.print(f"  후보: {result.candidate_count}개 → 스크리닝: {result.screened_count}개 → 추천: {len(result.recommendations)}개")
    console.print()

    if not result.recommendations:
        console.print("[yellow]추천 가능한 전형이 없습니다.[/yellow]")
        console.print("[dim]검색 키워드를 변경하거나 조건을 완화해보세요.[/dim]")
        raise typer.Exit(0)

    # Results table
    verdict_icons = {"안정": "🟢", "추천": "🔵", "도전": "🟡"}
    table = Table(title="추천 결과")
    table.add_column("#", justify="right", style="dim", width=3)
    table.add_column("대학", style="cyan", min_width=10)
    table.add_column("학과", style="green", min_width=10)
    table.add_column("전형", min_width=8)
    table.add_column("유형", width=6)
    table.add_column("정원", justify="right", width=4)
    table.add_column("평가", width=8)

    for i, rec in enumerate(result.recommendations, 1):
        icon = verdict_icons.get(rec.verdict, "⚪")
        table.add_row(
            str(i),
            rec.university,
            rec.department_name,
            rec.process_name,
            rec.process_type or "-",
            str(rec.quota) if rec.quota else "-",
            f"{icon} {rec.verdict}",
        )

    console.print(table)

    # Verbose: detailed panels
    if verbose:
        console.print()
        for i, rec in enumerate(result.recommendations, 1):
            icon = verdict_icons.get(rec.verdict, "⚪")
            title = f"{i}. {rec.university} {rec.department_name} - {rec.process_name} ({rec.process_type or '기타'}, {rec.admission_type or '미정'})"
            body_parts = []
            if rec.suneung_analysis:
                body_parts.append(f"[bold]수능 최저:[/bold] {rec.suneung_analysis}")
            if rec.gpa_analysis:
                body_parts.append(f"[bold]내신:[/bold] {rec.gpa_analysis}")
            if rec.overall_assessment:
                body_parts.append(f"[bold]종합:[/bold] {rec.overall_assessment}")
            body = "\n".join(body_parts) if body_parts else rec.overall_assessment or "분석 정보 없음"
            console.print(Panel(body, title=f"{icon} {title}", border_style="blue"))


if __name__ == "__main__":
    app()
