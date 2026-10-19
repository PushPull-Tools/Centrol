FROM python:3.14-alpine
WORKDIR /agent
COPY agent.py sources.py ./
EXPOSE 8323
CMD ["python", "-u", "agent.py"]
