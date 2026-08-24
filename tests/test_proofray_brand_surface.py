from proofray import (
    HorizonAnswerEngine,
    HorizonConfig,
    HorizonMemory,
    OpenTextHorizonMemory,
    ProofRayAnswerEngine,
    ProofRayConfig,
    ProofRayMemory,
    OpenTextProofRayMemory,
)


def test_proofray_namespace_preserves_legacy_object_identity():
    assert ProofRayMemory is HorizonMemory
    assert ProofRayConfig is HorizonConfig
    assert ProofRayAnswerEngine is HorizonAnswerEngine
    assert OpenTextProofRayMemory is OpenTextHorizonMemory
