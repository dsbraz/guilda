"""Prepara ADC isolado com impersonação; não altera o login padrão do gcloud."""

import argparse
import json
from pathlib import Path


def prepare(source: Path):
    project = json.loads((Path(__file__).resolve().parents[1] / "cloud.json").read_text())[
        "project"
    ]
    credentials = json.loads(source.read_text())
    if credentials.get("type") != "authorized_user":
        raise ValueError("a origem deve ser o ADC de usuário do projeto de spikes")
    credentials["quota_project_id"] = project
    target = Path(__file__).resolve().parents[1] / ".secrets"
    target.mkdir(mode=0o700, exist_ok=True)
    target.chmod(0o700)
    path = target / "adc.json"
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    path.write_text(
        json.dumps(
            {
                "type": "impersonated_service_account",
                "service_account_impersonation_url": "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/"
                f"spike-runner@{project}.iam.gserviceaccount.com:generateAccessToken",
                "source_credentials": credentials,
                "delegates": [],
                "quota_project_id": project,
            }
        )
    )
    print("ADC isolado preparado em .secrets/adc.json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    prepare(parser.parse_args().source)
