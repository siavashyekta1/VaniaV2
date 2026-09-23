from users.models import ExpertProfession

from .types import ValidationResult
from .validators import (
    ManualReviewValidator,
    MockPatternValidator,
    RealLawyerValidator,
    RealPsychologistValidator,
)


_VALIDATORS = {
    "mock": MockPatternValidator(),
    "mock_psychologist": MockPatternValidator(),
    "mock_psychiatrist": MockPatternValidator(),
    "mock_lawyer": MockPatternValidator(),
    "mock_general_doctor": MockPatternValidator(),
    "real_psychologist": RealPsychologistValidator(),
    "real_lawyer": RealLawyerValidator(),
    "manual_psychiatrist": ManualReviewValidator(),
    "manual_general_doctor": ManualReviewValidator(),
    "manual_psychology_student": ManualReviewValidator(),
}

_PROFESSION_DEFAULT_VALIDATORS = {
    "psychologist": "real_psychologist",
    "lawyer": "real_lawyer",
    "psychiatrist": "manual_psychiatrist",
    "general_doctor": "manual_general_doctor",
    "psychology_student": "manual_psychology_student",
}


def validate_profession_credential(
    profession: ExpertProfession,
    full_name: str,
    credential_code: str,
) -> ValidationResult:
    validator_key = _PROFESSION_DEFAULT_VALIDATORS.get(profession.slug) or profession.validation_kind
    validator = _VALIDATORS.get(validator_key) or _VALIDATORS.get(profession.validation_kind) or _VALIDATORS["mock"]
    return validator.validate(profession=profession, full_name=full_name, credential_code=credential_code)
