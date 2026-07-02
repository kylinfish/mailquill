"""設定與密碼清單載入。"""
from __future__ import annotations

import os
from dataclasses import dataclass

import yaml


@dataclass
class Config:
    label: str | list[str]
    csv_path: str = "transactions.csv"
    db_path: str = "mailquill.db"
    categories_path: str = "categories.yaml"
    rules_path: str = "rules.yaml"
    raw_dir: str = "raw"
    passwords_path: str = "passwords.txt"
    credentials_path: str = "credentials.json"
    token_path: str = "token.json"

    def label_names(self) -> list[str]:
        """label 可為單一字串或字串清單，一律正規化為清單。"""
        if isinstance(self.label, str):
            return [self.label]
        return list(self.label)


def load_config(path: str = "config.yaml") -> Config:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if "label" not in data or not data["label"]:
        raise ValueError("config 缺少必填欄位 'label'")
    allowed = {f for f in Config.__dataclass_fields__}
    kwargs = {k: v for k, v in data.items() if k in allowed}
    return Config(**kwargs)


def load_passwords(path: str) -> list[str]:
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if s and not s.startswith("#"):
                out.append(s)
    return out
