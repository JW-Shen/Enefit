"""
Exp6.

* Add historical weather features.

Author: JiaWei Jiang
"""
from typing import Dict

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch import Tensor


class _WaveBlock(nn.Module):
    def __init__(self, n_layers: int, in_dim: int, h_dim: int) -> None:
        super().__init__()

        self.n_layers = n_layers

        dilation_rates = [2**i for i in range(n_layers)]
        self.in_conv = nn.Conv1d(in_dim, h_dim, 1, padding="same")
        self.filts = nn.ModuleList()
        self.gates = nn.ModuleList()
        self.skip_convs = nn.ModuleList()
        for layer in range(n_layers):
            self.filts.append(nn.Conv1d(h_dim, h_dim, 3, padding="same", dilation=dilation_rates[layer]))
            self.gates.append(nn.Conv1d(h_dim, h_dim, 3, padding="same", dilation=dilation_rates[layer]))
            self.skip_convs.append(nn.Conv1d(h_dim, h_dim, 1, padding="same"))

        self.dropout = nn.Dropout(0.2)

    def forward(self, x: Tensor) -> Tensor:
        """Forward pass.

        Shape:
            x: (B, C_in, T)
        """
        x = self.in_conv(x)  # (B, H, T)
        x_resid = x

        for layer in range(self.n_layers):
            x_filt = self.filts[layer](x)
            x_gate = self.gates[layer](x)
            x = F.tanh(x_filt) * F.sigmoid(x_gate)
            x = self.skip_convs[layer](x)

            x = self.dropout(x)
            x_resid = x_resid + x

        return x_resid


class Exp(nn.Module):
    """Exp6."""

    def __init__(self) -> None:
        self.name = self.__class__.__name__
        super().__init__()

        # Model blocks
        self.wave_block1 = _WaveBlock(8, 5, 64)
        self.wave_block2 = _WaveBlock(5, 78, 64)

        self.gn1 = nn.GroupNorm(4, 64)
        self.gn2 = nn.GroupNorm(4, 64)

        self.avg_pool = nn.AvgPool1d(24, stride=1)
        self.max_pool = nn.MaxPool1d(24, stride=1)

        self.prod_head = nn.Sequential(
            nn.Linear(147, 128),
            nn.ReLU(),
            nn.Linear(128, 64),
            nn.ReLU(),
            nn.Linear(64, 1),
        )
        self.cons_head = nn.Sequential(
            nn.Linear(147, 128),
            nn.ReLU(),
            nn.Linear(128, 64),
            nn.ReLU(),
            nn.Linear(64, 1),
        )
        self.prod_lin = nn.Linear(6, 1)
        self.cons_lin = nn.Linear(6, 1)
        self.prod_lin.bias = nn.Parameter(torch.tensor([0.000388]))
        self.cons_lin.bias = nn.Parameter(torch.tensor([0.161311]))

    def forward(self, inputs: Dict[str, Tensor]) -> Tensor:
        """Forward pass.

        Args:
            inputs: model inputs

        Shape:
            x: (B, 2, T)
            hwth: (B, C, T), C=14
            tids: (B, 7, 24)
            cli_attr: (B, 3)
            fwth: (B, C, 24), C=12

        Returns:
            output: prediction
        """
        x, hwth = inputs["x"], inputs["hwth"]
        tids, cli_attr, fwth = inputs["tids"], inputs["cli_attr"], inputs["fwth"]
        batch_size, _, t_window = x.shape
        x_bypass = x

        cli_attr = cli_attr.unsqueeze(dim=-1).expand(-1, -1, t_window)
        x = torch.cat([x, cli_attr], dim=1)  # (B, 5, T)
        x = self.wave_block1(x)  # (B, 64, T)
        x = self.gn1(x)

        x = torch.cat([x, hwth], dim=1)  # (B, 78, T)
        x = self.wave_block2(x)
        x = self.gn2(x)

        x = torch.cat(
            [x[..., -24:], self.avg_pool(x[:, :32])[..., -24:], self.max_pool(x[:, 32:])[..., -24:]], axis=1
        )  # (B, 128, 24)

        # Non-linear
        x_p = torch.cat([x, tids, fwth], dim=1)  # (B, 147, 24)
        x_c = torch.cat([x, tids, fwth], dim=1)
        op_nl = self.prod_head(x_p.transpose(1, 2))  # (B, 24, 1)
        oc_nl = self.cons_head(x_c.transpose(1, 2))  # (B, 24, 1)

        # Linear
        x_bypass = x_bypass.reshape(batch_size, 2, 6, 24).transpose(2, 3)  # (B, 2, 24, 6)
        op_l = self.prod_lin(x_bypass[:, 0])
        oc_l = self.cons_lin(x_bypass[:, 1])
        output_p, output_c = op_nl + op_l, oc_nl + oc_l

        output = torch.concat([output_p, output_c], dim=-1).reshape(batch_size, -1)  # (B, 48)

        return output
