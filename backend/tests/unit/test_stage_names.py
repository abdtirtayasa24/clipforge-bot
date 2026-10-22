from clipforge.pipeline.stages.affiliate import AffiliateStage
from clipforge.pipeline.stages.analyze import AnalyzeStage
from clipforge.pipeline.stages.audio import AudioStage
from clipforge.pipeline.stages.ingest import IngestStage
from clipforge.pipeline.stages.render import RenderStage
from clipforge.pipeline.stages.select import SelectStage
from clipforge.pipeline.stages.transcribe import TranscribeStage
from clipforge.pipeline.stages.upload import UploadStage

STAGES = [
    IngestStage,
    AudioStage,
    TranscribeStage,
    AnalyzeStage,
    SelectStage,
    RenderStage,
    UploadStage,
    AffiliateStage,
]


def test_every_stage_records_a_distinct_running_name() -> None:
    """A running name shared with any other name would resume the job at the wrong stage."""
    names = [stage.name for stage in STAGES]
    running_names = [stage.running_name for stage in STAGES]

    assert None not in running_names
    assert len(set(names + running_names)) == 2 * len(STAGES)
