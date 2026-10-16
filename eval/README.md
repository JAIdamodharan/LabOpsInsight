# Evaluation

26 questions graded deterministically against golden SQL on the synthetic lab data (no model judges the model).

```bash
python -m eval.run_eval --provider groq --model openai/gpt-oss-120b            # all questions
python -m eval.run_eval --provider groq --ids q01,q07,q18                       # a few
```

It prints one line per question, writes `eval/results/<stamp>.partial.json` after each, and Ctrl+C still saves a report. Free tiers pause for rate limits, so a full run can take a while. Results are git-ignored.
