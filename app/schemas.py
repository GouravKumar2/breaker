from enum import Enum
from pydantic import BaseModel, Field


class Category(str, Enum):
    boundary = "boundary"
    overflow = "overflow"
    empty = "empty"
    duplicates = "duplicates"
    max_size = "max-size"
    tricky_negative = "tricky-negative"
    other = "other"


class TestCase(BaseModel):
    input: str = Field(description="Exact stdin contents for the program, including newlines. Keep under 1500 characters. Leave EMPTY if generator_code is used.")
    generator_code: str = Field(default="", description="Optional Python 3 program that PRINTS the full input to stdout, for large inputs (e.g. n=1000). Leave empty for small inputs.")
    reasoning: str = Field(description="One short sentence: what bug this input targets")
    category: Category


class TestSuite(BaseModel):
    tests: list[TestCase]


class BruteForce(BaseModel):
    code: str = Field(description="Complete Python 3 program reading stdin, writing stdout")
    notes: str = Field(description="One sentence on the approach")


class ValidatorCode(BaseModel):
    code: str = Field(description="Complete Python 3 program: reads stdin, exits 0 if input is valid, exits 1 otherwise, prints nothing")
    notes: str = Field(description="One sentence listing the constraints it checks")


class Driver(BaseModel):
    code: str = Field(description="Driver code: for C++ a complete main() function only; for Python a complete script tail that reads stdin, calls Solution, prints the result")
    notes: str = Field(description="One sentence on how it parses stdin")


class SpecExample(BaseModel):
    input: str = Field(description="Example input in the canonical stdin format, exact text with newlines")
    output: str = Field(description="Expected stdout for that input")


class ProblemSpec(BaseModel):
    task: str = Field(description="What to compute, 1-3 sentences, no story")
    input_format: str = Field(description="Exact stdin format, line by line")
    output_format: str = Field(description="Exact stdout format")
    constraints: str = Field(description="All constraints and value ranges, verbatim from the statement")
    examples: list[SpecExample] = Field(description="1-3 examples converted to the canonical stdin/stdout format")
    multiple_valid_answers: bool = Field(description="True if several different outputs could be correct for the same input")