# Tasks

### Turn Detection

- [x] Write main class.
- [ ] Set up logging.
- [ ] Log latency.
- [ ] Run test for this feature.

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
python3 -m venv venv
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
Commit Convention: https://www.conventionalcommits.org/en/v1.0.0/?fbclid=IwY2xjawJVBFVleHRuA2FlbQIxMAABHVQQJaQ9FxqBj1krDH_GGIBrb1FFmivfoCZOmnTwyDGhTbuU7ePhUkBjXg_aem_BovCkvsOv1Rhn6rh61aB_g

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

https://github.com/resemble-ai/chatterbox

https://theneuralmaze.substack.com/p/a-hands-on-project-to-master-multimodal

https://www.youtube.com/watch?v=c19PrP3bd6Y

https://github.com/patchy631/ai-engineering-hub/tree/main/mcp-voice-agent

https://www.deeplearning.ai/short-courses/building-ai-voice-agents-for-production/