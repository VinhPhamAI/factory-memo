from transformers import AutoTokenizer
import onnxruntime as ort
import numpy as np
from pathlib import Path
import time
import string

UNLIKELY_THRESHOLD = 0.15
MAX_HISTORY_TOKENS = 2048

class EndOfTurnPredictor:
    def __init__(self, model_path: str = 'models/model_quantized.onnx', tokenizer_path: str = 'models'):
        self.model_path = model_path
        self.tokenizer_path = tokenizer_path
        self.tokenizer = None
        self.session = None
        self.eou_index = None

        self._initialize()

    def _initialize(self):
        """
        Load the ONNX model and tokenizer, and identify the EOU token index.
        """
        try:
            start_time = time.time()
            self.session = ort.InferenceSession(str(self.model_path))
            self.tokenizer = AutoTokenizer.from_pretrained(self.tokenizer_path)
            self.eou_index = self.tokenizer.encode("<|im_end|>")[0]
            print(f"Model initialized in {time.time() - start_time:.2f} seconds")
        except Exception as e:
            raise RuntimeError(f"Model initialization failed: {e}")

    @staticmethod
    def _normalize(text: str) -> str:
        """
        Normalize text by removing punctuation (excluding single quotes) and normalizing whitespace.
        """
        PUNCS = string.punctuation.replace("'", "")
        stripped = ''.join(char for char in text if char not in PUNCS)
        return ' '.join(stripped.lower().split())

    def _format_chat_context(self, chat_context: list[dict]) -> str:
        """
        Prepare the chat context into a formatted string for the model.
        """
        normalized_context = [
            {**msg, 'content': self._normalize(msg['content'])}
            for msg in chat_context if msg.get('content')
        ]

        convo_text = self.tokenizer.apply_chat_template(
            normalized_context,
            add_generation_prompt=True,
            add_special_tokens=False,
            tokenize=False
        )

        eou_token = "<|im_end|>"
        last_eou_index = convo_text.rfind(eou_token)
        return convo_text[:last_eou_index] if last_eou_index >= 0 else convo_text

    @staticmethod
    def _softmax(logits: np.ndarray) -> np.ndarray:
        """
        Compute softmax probabilities from logits.
        """
        exp_logits = np.exp(logits - np.max(logits))
        return exp_logits / exp_logits.sum()

    def predict(self, chat_context: list[dict]) -> float:
        """
        Predict the probability that the current user turn has ended.
        """
        formatted_text = self._format_chat_context(chat_context)

        inputs = self.tokenizer(
            formatted_text,
            add_special_tokens=False,
            return_tensors="np",
            max_length=MAX_HISTORY_TOKENS,
            truncation=True,
        )

        input_ids = np.array(inputs["input_ids"], dtype=np.int64)
        output = self.session.run(["logits"], {"input_ids": input_ids})
        logits = output[0]
        last_token_logits = logits[0, -1]
        probs = self._softmax(last_token_logits)

        return float(probs[self.eou_index])
