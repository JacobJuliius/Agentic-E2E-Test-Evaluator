from pathlib import Path


def _artifact_dir(context):
    out = Path(context.config.userdata.get("artifact_dir", "artifacts"))
    out.mkdir(parents=True, exist_ok=True)
    return out


def after_step(context, step):
    if step.status == "failed" and hasattr(context, "driver"):
        try:
            out = _artifact_dir(context)
            context.driver.save_screenshot(str(out / "failure.png"))
            (out / "page_source.html").write_text(context.driver.page_source, encoding="utf-8")
        except Exception:
            pass


def after_scenario(context, scenario):
    if hasattr(context, "driver"):
        try:
            context.driver.quit()
        except Exception:
            pass
