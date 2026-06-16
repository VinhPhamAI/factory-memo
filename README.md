# Tasks

### Turn Detection

- [x] Write main class.
- [ ] Implement Voice Activity Detection
- [ ] Implement Semantic turn detection
- [ ] Integrate the full pipeline of Semantic Turn Detection and Voice Activity Detection into the main class.
- [ ] Set up logging.
- [ ] Log latency.
- [ ] Run test for this pipeline.

### Voice Activity Detection 
- [ ] Write main class
- [ ] Set up logging.
- [ ] Log latency.
- [ ] Run test for this feature.

### Text to speech
- [ ] Write main class
- [ ] Set up logging.
- [ ] Log latency.
- [ ] Run test for this feature.

### Speech to text
- [ ] Write main class
- [ ] Set up logging.
- [ ] Log latency.
- [ ] Run test for this feature.

# Project Structure

backend/app.py: Run backend api (not done yet)
backend/src/*: Contain main logic of seperate voice and llm service.
backend/utils/*: Contain some helper function, schema, and other thing stuff.
backend/router/*: Router for api
backend/api/*: API


# Installation & Usage
Start backend
```
cd backend
uv venv --python 3.12
source venv/bin/activate
pip install -r requirements.txt
```

Start frontend
```
cd frontend
npm install
npm run dev
```

### Git Command

Create a new branch
```
git checkout -b <branch-name>
```

Push to branch
```
git add .
git commit -m "feat: add feature"
git push origin <branch-name>
```

# References

https://voiceaiandvoiceagents.com/

https://arxiv.org/pdf/2501.06282

https://theneuralmaze.substack.com/p/a-hands-on-project-to-master-multimodal

https://www.youtube.com/watch?v=c19PrP3bd6Y

https://www.deeplearning.ai/short-courses/building-ai-voice-agents-for-production/