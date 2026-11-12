// SPDX-License-Identifier: GPL-2.0-only OR MIT

//! Identity of one owned ADT copy, independent of its Rust object's address.
//! This is not a physical boot identifier, read lease or freshness proof.

use kernel::{prelude::*, sync::Arc};

#[derive(Clone)]
pub(crate) struct Source(Arc<()>);

impl Source {
    pub(super) fn new() -> Result<Self> {
        Ok(Self(Arc::new((), GFP_KERNEL)?))
    }

    pub(crate) fn same(&self, other: &Self) -> bool {
        Arc::ptr_eq(&self.0, &other.0)
    }
}
