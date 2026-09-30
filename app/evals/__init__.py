"""
Evals package.

The evaluation framework measures system quality across:
    - Outreach quality (open rate, reply rate, positive sentiment)
    - Qualification accuracy (meetings booked vs. leads contacted)
    - Agent decision quality (LLM-as-judge on sampled decisions)
    - Pipeline velocity (time from lead to meeting)

Evals run asynchronously and write results to the database.
They feed the RevOps learning loop.

Nothing here yet — eval framework is implemented in a later step.
"""
