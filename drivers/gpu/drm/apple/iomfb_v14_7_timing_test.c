// SPDX-License-Identifier: GPL-2.0-only OR MIT
/* Exercise the internal timing path with a fake RPC transport, not a DCP. */
struct v14_timing_test {
	struct kunit *test;
	struct v14_bl_fixture f;
	struct dcp_display_mode mode;
	struct drm_crtc_state state;
	unsigned int calls;
	u32 color, timing;
	int transport_error;
	u32 status;
};

static struct v14_timing_test *v14_timing_current;

static int v14_timing_fake_call(struct apple_dcp_v14 *v14, u32 tag, const void *in, u32 in_size,
				void *out, u32 out_size, u32 completion,
				dcp_v14_admit_fn admit, void *admit_cookie)
{
	struct v14_timing_test *c = v14_timing_current;

	KUNIT_EXPECT_TRUE(c->test, mutex_is_locked(&v14->lock));
	KUNIT_EXPECT_EQ(c->test, tag, A(411));
	KUNIT_EXPECT_EQ(c->test, in_size, 8U);
	KUNIT_EXPECT_EQ(c->test, out_size, 4U);
	KUNIT_EXPECT_EQ(c->test, completion, 0U);
	if (in_size != 8 || out_size != 4)
		return -EINVAL;
	c->calls++;
	c->color = get_unaligned_le32(in);
	c->timing = get_unaligned_le32((const u8 *)in + 4);
	put_unaligned_le32(c->status, out);
	return c->transport_error;
}

static struct v14_timing_test *v14_timing_setup(struct kunit *test)
{
	struct v14_timing_test *c = kunit_kzalloc(test, sizeof(*c), GFP_KERNEL);

	KUNIT_ASSERT_NOT_NULL(test, c);
	c->test = test;
	c->f = v14_bl_fixture(test, &dcp_v14_board_j514m, false);
	mutex_init(&c->f.dcp->modes_lock);
	spin_lock_init(&c->f.dcp->metadata.lock);
	c->mode.mode.clock = 120000;
	c->mode.mode.hdisplay = 800;
	c->mode.mode.htotal = 1000;
	c->mode.mode.vdisplay = 600;
	c->mode.mode.vtotal = 1000;
	c->mode.color_mode_id = 0x1234;
	c->mode.timing_mode_id = 0x5678;
	c->state.mode = c->mode.mode;
	c->f.dcp->modes = &c->mode;
	c->f.dcp->nr_modes = 1;
	c->f.dcp->modes_admitted = true;
	v14_timing_current = c;
	kunit_activate_static_stub(test, dcp_v14_call_guarded, v14_timing_fake_call);
	return c;
}

static void v14_timing_optin_and_reply(struct kunit *test)
{
	struct v14_timing_test *c = v14_timing_setup(test);

	KUNIT_ASSERT_EQ(test, drm_mode_vrefresh(&c->mode.mode), 120);
	KUNIT_EXPECT_EQ(test, dcp_v14_internal_modeset(c->f.dcp, &c->state, false), 0);
	KUNIT_EXPECT_EQ(test, c->calls, 0U);
	KUNIT_EXPECT_FALSE(test, c->f.v14->j514m_timing_replied);
	KUNIT_ASSERT_EQ(test, dcp_v14_internal_modeset(c->f.dcp, &c->state, true), 0);
	KUNIT_EXPECT_EQ(test, c->calls, 1U);
	KUNIT_EXPECT_EQ(test, c->color, 0x1234U);
	KUNIT_EXPECT_EQ(test, c->timing, 0x5678U);
	KUNIT_EXPECT_TRUE(test, c->f.v14->j514m_timing_replied);
	KUNIT_EXPECT_TRUE(test, c->f.dcp->mode_state.valid);
	KUNIT_EXPECT_EQ(test, dcp_v14_internal_modeset(c->f.dcp, &c->state, true), 0);
	KUNIT_EXPECT_EQ(test, c->calls, 1U);
}

static void v14_timing_refused(struct kunit *test)
{
	struct v14_timing_test *c = v14_timing_setup(test);

	c->status = 1;
	c->f.dcp->mode_state.valid = true;
	KUNIT_EXPECT_EQ(test, dcp_v14_internal_modeset(c->f.dcp, &c->state, true), -EIO);
	KUNIT_EXPECT_EQ(test, c->calls, 1U);
	KUNIT_EXPECT_TRUE(test, c->f.v14->failed);
	KUNIT_EXPECT_FALSE(test, c->f.v14->j514m_timing_replied);
	KUNIT_EXPECT_FALSE(test, c->f.dcp->mode_state.valid);
	KUNIT_EXPECT_TRUE(test, c->f.dcp->mode_state.invalidated);
	KUNIT_EXPECT_EQ(test, dcp_v14_internal_modeset(c->f.dcp, &c->state, true), -EIO);
	KUNIT_EXPECT_EQ(test, c->calls, 1U);
}

static void v14_timing_wrong_mode(struct kunit *test)
{
	struct v14_timing_test *c = v14_timing_setup(test);

	c->state.mode.hdisplay++;
	KUNIT_EXPECT_EQ(test, dcp_v14_internal_modeset(c->f.dcp, &c->state, true), -EINVAL);
	KUNIT_EXPECT_EQ(test, c->calls, 0U);
}

static void v14_timing_transport_failure(struct kunit *test)
{
	struct v14_timing_test *c = v14_timing_setup(test);

	c->transport_error = -ETIMEDOUT;
	c->f.dcp->mode_state.valid = true;
	KUNIT_EXPECT_EQ(test, dcp_v14_internal_modeset(c->f.dcp, &c->state, true), -ETIMEDOUT);
	KUNIT_EXPECT_EQ(test, c->calls, 1U);
	KUNIT_EXPECT_TRUE(test, c->f.v14->failed);
	KUNIT_EXPECT_FALSE(test, c->f.dcp->mode_state.valid);
	KUNIT_EXPECT_FALSE(test, c->f.v14->j514m_timing_replied);
}

static void v14_timing_60hz(struct kunit *test)
{
	struct v14_timing_test *c = v14_timing_setup(test);

	c->mode.mode.clock = 60000;
	c->state.mode = c->mode.mode;
	KUNIT_ASSERT_EQ(test, drm_mode_vrefresh(&c->mode.mode), 60);
	KUNIT_EXPECT_EQ(test, dcp_v14_internal_modeset(c->f.dcp, &c->state, true), 0);
	KUNIT_EXPECT_EQ(test, c->calls, 0U);
}

static struct kunit_case v14_timing_cases[] = {
	KUNIT_CASE(v14_timing_optin_and_reply),
	KUNIT_CASE(v14_timing_refused),
	KUNIT_CASE(v14_timing_wrong_mode),
	KUNIT_CASE(v14_timing_transport_failure),
	KUNIT_CASE(v14_timing_60hz),
	{}
};

static struct kunit_suite v14_timing_suite = {
	.name = "apple-dcp-v14-j514m-timing",
	.test_cases = v14_timing_cases,
};
kunit_test_suite(v14_timing_suite);
