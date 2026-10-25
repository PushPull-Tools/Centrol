# Centrol Agent pour Home Assistant OS : même agent que sur les autres serveurs (bibliothèque standard Python seule).
FROM python:3.14-alpine
WORKDIR /agent
COPY agent.py sources.py procs.py run.py ./
CMD ["python", "-u", "run.py"]
