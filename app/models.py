import hashlib
from datetime import datetime, timezone

from sqlalchemy import (JSON, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def now():
    return datetime.now(timezone.utc)


def sha(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


class Problem(Base):
    __tablename__ = "problems"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    text_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    statement: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    solutions: Mapped[list["Solution"]] = relationship(back_populates="problem")


class Solution(Base):
    __tablename__ = "solutions"
    __table_args__ = (UniqueConstraint("problem_id", "code_hash", "language"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    problem_id: Mapped[int] = mapped_column(ForeignKey("problems.id"), index=True)
    language: Mapped[str] = mapped_column(String(16))
    code_hash: Mapped[str] = mapped_column(String(64))
    code: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    problem: Mapped[Problem] = relationship(back_populates="solutions")
    runs: Mapped[list["Run"]] = relationship(back_populates="solution")


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (Index("ix_runs_verdict_created", "verdict", "created_at"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    solution_id: Mapped[int] = mapped_column(ForeignKey("solutions.id"), index=True)
    strategy: Mapped[str] = mapped_column(String(32))
    verdict: Mapped[str] = mapped_column(String(16))
    runtime_s: Mapped[float] = mapped_column(Float, default=0.0)
    total_tests: Mapped[int] = mapped_column(Integer, default=0)
    invalid_tests: Mapped[int] = mapped_column(Integer, default=0)
    oracle_rebuilds: Mapped[int] = mapped_column(Integer, default=0)
    failing_input: Mapped[str | None] = mapped_column(Text, nullable=True)
    minimized_input: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    actual_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_verdict: Mapped[str | None] = mapped_column(String(24), nullable=True)
    steps: Mapped[list | None] = mapped_column(JSON, nullable=True)
    reference_notes: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    solution: Mapped[Solution] = relationship(back_populates="runs")
    tests: Mapped[list["GeneratedTest"]] = relationship(back_populates="run", cascade="all, delete-orphan")
    results: Mapped[list["RunResult"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class GeneratedTest(Base):
    __tablename__ = "generated_tests"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)
    input: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(24), index=True)
    reasoning: Mapped[str] = mapped_column(Text, default="")
    run: Mapped[Run] = relationship(back_populates="tests")


class RunResult(Base):
    __tablename__ = "run_results"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)
    input: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(24))
    verdict: Mapped[str] = mapped_column(String(24), index=True)
    expected: Mapped[str] = mapped_column(Text, default="")
    actual: Mapped[str] = mapped_column(Text, default="")
    sol_runtime_ms: Mapped[float] = mapped_column(Float, default=0.0)
    run: Mapped[Run] = relationship(back_populates="results")