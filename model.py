import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import Optional

class CPUOptimizedTransformer(nn.Module):
    def __init__(self, layers=16, hidden_size=768, quantization_level='int8'):
        super().__init__()
        self.layers = layers
        self.hidden_size = hidden_size
        
        # Initialize transformer components with CPU-specific settings
        torch.set_num_threads(4)  # Optimize CPU thread usage
        torch.set_num_interop_threads(1)  # Optimize interop threads
        
        self.encoder = self._build_encoder()
        self.decoder = self._build_decoder()
        
        # Quantize model if specified using standard PyTorch quantization
        if quantization_level == 'int8':
            self.quantize_model()
    
    def _build_encoder(self):
        return nn.ModuleList([
            self._create_optimized_encoder_layer() 
            for _ in range(self.layers)
        ])
    
    def _create_optimized_encoder_layer(self):
        """Create quantization-friendly encoder layer"""
        layer = nn.TransformerEncoderLayer(
            d_model=self.hidden_size,
            nhead=8,
            dim_feedforward=self.hidden_size * 4,
            batch_first=True,
            norm_first=True  # Better performance on CPU
        )
        # Ensure layer components are quantization-ready
        layer.linear1 = nn.Sequential(
            layer.linear1,
            nn.ReLU()  # Add explicit ReLU for better quantization
        )
        # Enable fast path for better CPU performance
        layer.norm1.eps = 1e-5
        layer.norm2.eps = 1e-5
        return layer
    
    def _build_decoder(self):
        return nn.ModuleList([
            nn.TransformerDecoderLayer(
                d_model=self.hidden_size,
                nhead=8,
                dim_feedforward=self.hidden_size * 4,
                batch_first=True
            ) for _ in range(self.layers)
        ])
    
    def quantize_model(self):
        """Implement symmetric quantization for CPU with proper calibration"""
        # Set up symmetric quantization config
        self.qconfig = torch.quantization.QConfig(
            activation=torch.quantization.observer.MinMaxObserver.with_args(
                qscheme=torch.per_tensor_symmetric,  # Changed to symmetric
                dtype=torch.quint8,
                quant_min=0,
                quant_max=255,
            ),
            weight=torch.quantization.observer.PerChannelMinMaxObserver.with_args(
                dtype=torch.qint8,
                qscheme=torch.per_channel_symmetric,  # Changed to symmetric
                ch_axis=0,
                quant_min=-128,
                quant_max=127
            )
        )
        
        # Prepare model for quantization
        self.train()  # Set to training mode for calibration
        torch.quantization.prepare(self, inplace=True)
        
        # Calibrate with dummy data
        self._calibrate_model()
        
        # Run calibration before converting
        self._run_calibration()
        
        # Convert to quantized model
        self.eval()  # Set to eval mode before conversion
        torch.quantization.convert(self, inplace=True)
    
    def _calibrate_model(self, num_batches=10):
        """Calibrate model with dummy data"""
        self.eval()
        with torch.no_grad():
            for _ in range(num_batches):
                # Create dummy batch with correct shape
                dummy_input = torch.randn(
                    2,  # batch_size
                    32,  # sequence_length
                    self.hidden_size,  # embedding_dim
                    device='cpu'
                )
                _ = self(dummy_input)
    
    def _run_calibration(self, num_batches=100):
        """Run proper calibration with dummy data"""
        self.eval()
        with torch.no_grad():
            for _ in range(num_batches):
                # Create dummy batch for calibration
                dummy_input = torch.randn(
                    4,  # batch_size
                    32,  # sequence_length
                    self.hidden_size,  # embedding_dim
                    device='cpu'
                )
                _ = self(dummy_input)
                
                # Force observer update
                for module in self.modules():
                    if hasattr(module, 'observer_enabled'):
                        module.calculate_qparams()

    def forward(self, src, tgt=None):
        """
        Forward pass with shape handling
        Args:
            src: Input tensor of shape (batch_size, seq_len) or (batch_size, seq_len, hidden_size)
            tgt: Optional target tensor for training
        """
        # Handle input shape
        if len(src.shape) == 2:
            # Add embedding dimension
            src = src.unsqueeze(-1).expand(-1, -1, self.hidden_size)
        
        # Validate input shape
        batch_size, seq_len, hidden_size = src.shape
        if hidden_size != self.hidden_size:
            raise ValueError(f"Input hidden size {hidden_size} doesn't match model hidden size {self.hidden_size}")

        # Process through encoder
        encoder_output = src
        for enc_layer in self.encoder:
            encoder_output = enc_layer(encoder_output)
        
        # Handle decoder if in training mode
        if self.training and tgt is not None:
            if len(tgt.shape) == 2:
                tgt = tgt.unsqueeze(-1).expand(-1, -1, self.hidden_size)
            decoder_output = tgt
            for dec_layer in self.decoder:
                decoder_output = dec_layer(
                    decoder_output,
                    encoder_output
                )
            return decoder_output
        
        return encoder_output