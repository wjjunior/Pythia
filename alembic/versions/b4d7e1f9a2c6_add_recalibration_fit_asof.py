"""add recalibration fit_asof guard columns (PYT-111)

recalibration_models.fit_asof = the logical asof a map was fit on (distinct from fitted_at, the
wall-clock write time). estimates.p_model_calibrated_fit_asof = the fit_asof of the map applied to
the committed forecast (NULL = raw, no map). Both let the pipeline and the verdict refuse a map fit
after the snapshot it forecasts — replay/backtest lookahead (§4.9, AC-13).

Revision ID: b4d7e1f9a2c6
Revises: 43ff4d886861
Create Date: 2026-06-26 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4d7e1f9a2c6"
down_revision: str | Sequence[str] | None = "43ff4d886861"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "estimates",
        sa.Column("p_model_calibrated_fit_asof", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "recalibration_models",
        sa.Column("fit_asof", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("recalibration_models", "fit_asof")
    op.drop_column("estimates", "p_model_calibrated_fit_asof")
