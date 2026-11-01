// SPDX-License-Identifier: GPL-2.0-only OR MIT
/*
 * Included by pmgr-pwrstate.c: apple,no-auto-pm on the power-on and
 * power-off paths. The power state register is emulated: its actual state
 * follows the target written, unless the test holds it where it is.
 */
#include <kunit/device.h>
#include <kunit/test.h>

struct pmgr_ps_test_reg {
	u32 val;
	bool stuck;		/* the actual state no longer follows the target */
	bool disable_stuck;	/* the disable bit does not latch */
	unsigned int auto_writes;	/* writes that enabled automatic PM */
	unsigned int writes;
	unsigned int fail_write;
	bool fail_read;
};

static int pmgr_ps_test_read(void *ctx, unsigned int reg, unsigned int *val)
{
	struct pmgr_ps_test_reg *r = ctx;

	if (r->fail_read)
		return -EIO;
	*val = r->val;
	return 0;
}

static int pmgr_ps_test_write(void *ctx, unsigned int reg, unsigned int val)
{
	struct pmgr_ps_test_reg *r = ctx;
	u32 actual = r->stuck ? FIELD_GET(APPLE_PMGR_PS_ACTUAL, r->val) :
				FIELD_GET(APPLE_PMGR_PS_TARGET, val);

	r->writes++;
	if (r->writes == r->fail_write)
		return -EIO;
	if (r->disable_stuck)
		val = (val & ~APPLE_PMGR_DEV_DISABLE) | (r->val & APPLE_PMGR_DEV_DISABLE);
	if (val & APPLE_PMGR_AUTO_ENABLE)
		r->auto_writes++;
	r->val = (val & ~APPLE_PMGR_PS_ACTUAL) | FIELD_PREP(APPLE_PMGR_PS_ACTUAL, actual);
	return 0;
}

static const struct regmap_config pmgr_ps_test_regmap = {
	.reg_bits = 32,
	.val_bits = 32,
	.reg_stride = 4,
	.reg_read = pmgr_ps_test_read,
	.reg_write = pmgr_ps_test_write,
	.fast_io = true,
};

/* A domain in power state @pstate, actual and target, on a device of its own */
static struct apple_pmgr_ps *pmgr_ps_test_domain(struct kunit *test, u32 pstate,
						 bool no_auto_pm, struct pmgr_ps_test_reg **reg)
{
	static const char *const names[] = { "apple-pmgr-ps-auto", "apple-pmgr-ps-no-auto" };
	struct pmgr_ps_test_reg *r;
	struct apple_pmgr_ps *ps;
	struct device *dev;

	r = kunit_kzalloc(test, sizeof(*r), GFP_KERNEL);
	KUNIT_ASSERT_NOT_NULL(test, r);
	r->val = FIELD_PREP(APPLE_PMGR_PS_TARGET, pstate) |
		 FIELD_PREP(APPLE_PMGR_PS_ACTUAL, pstate);
	dev = kunit_device_register(test, names[no_auto_pm]);
	KUNIT_ASSERT_NOT_ERR_OR_NULL(test, dev);
	ps = kunit_kzalloc(test, sizeof(*ps), GFP_KERNEL);
	KUNIT_ASSERT_NOT_NULL(test, ps);
	ps->dev = dev;
	ps->genpd.name = "test";
	ps->no_auto_pm = no_auto_pm;
	ps->regmap = devm_regmap_init(dev, NULL, r, &pmgr_ps_test_regmap);
	KUNIT_ASSERT_NOT_ERR_OR_NULL(test, ps->regmap);
	*reg = r;
	return ps;
}

/* Powering on enables automatic PM, unless the domain is apple,no-auto-pm. */
static void pmgr_ps_power_on_test(struct kunit *test)
{
	struct pmgr_ps_test_reg *r;
	struct apple_pmgr_ps *ps;
	bool no_auto_pm;

	for (no_auto_pm = false; ; no_auto_pm = true) {
		ps = pmgr_ps_test_domain(test, APPLE_PMGR_PS_PWRGATE, no_auto_pm, &r);
		KUNIT_EXPECT_EQ(test, apple_pmgr_ps_power_on(&ps->genpd), 0);
		KUNIT_EXPECT_EQ(test, FIELD_GET(APPLE_PMGR_PS_TARGET, r->val),
				APPLE_PMGR_PS_ACTIVE);
		KUNIT_EXPECT_EQ(test, FIELD_GET(APPLE_PMGR_PS_ACTUAL, r->val),
				APPLE_PMGR_PS_ACTIVE);
		KUNIT_EXPECT_EQ_MSG(test, !!(r->val & APPLE_PMGR_AUTO_ENABLE), !no_auto_pm,
				    "no_auto_pm %d", no_auto_pm);
		KUNIT_EXPECT_EQ_MSG(test, r->auto_writes, no_auto_pm ? 0U : 1U,
				    "no_auto_pm %d", no_auto_pm);
		if (no_auto_pm)
			break;
	}
}

/* A failed initial read must not be misreported as an inactive domain. */
static void pmgr_ps_initial_state_test(struct kunit *test)
{
	struct pmgr_ps_test_reg *r;
	struct apple_pmgr_ps *ps;
	bool active = true;

	ps = pmgr_ps_test_domain(test, APPLE_PMGR_PS_PWRGATE, false, &r);
	KUNIT_ASSERT_EQ(test, apple_pmgr_ps_is_active(ps, &active), 0);
	KUNIT_EXPECT_FALSE(test, active);
	r->val = FIELD_PREP(APPLE_PMGR_PS_ACTUAL, APPLE_PMGR_PS_ACTIVE);
	KUNIT_ASSERT_EQ(test, apple_pmgr_ps_is_active(ps, &active), 0);
	KUNIT_EXPECT_TRUE(test, active);
	r->val = APPLE_PMGR_AUTO_ENABLE | FIELD_PREP(APPLE_PMGR_PS_TARGET, APPLE_PMGR_PS_ACTIVE);
	KUNIT_ASSERT_EQ(test, apple_pmgr_ps_is_active(ps, &active), 0);
	KUNIT_EXPECT_TRUE(test, active);
	r->fail_read = true;
	active = true;
	KUNIT_EXPECT_EQ(test, apple_pmgr_ps_is_active(ps, &active), -EIO);
	KUNIT_EXPECT_TRUE(test, active);
	KUNIT_EXPECT_EQ(test, r->writes, 0U);
}

static void pmgr_ps_min_state_error_test(struct kunit *test)
{
	struct pmgr_ps_test_reg *r;
	struct apple_pmgr_ps *ps;

	ps = pmgr_ps_test_domain(test, APPLE_PMGR_PS_PWRGATE, false, &r);
	ps->min_state = APPLE_PMGR_PS_CLKGATE;
	r->fail_read = true;
	KUNIT_EXPECT_EQ(test, apple_pmgr_ps_program_min(ps), -EIO);
	KUNIT_EXPECT_EQ(test, r->writes, 0U);
	r->fail_read = false;
	r->fail_write = 1;
	KUNIT_EXPECT_EQ(test, apple_pmgr_ps_program_min(ps), -EIO);
	KUNIT_EXPECT_EQ(test, r->writes, 1U);
	r->fail_write = 0;
	KUNIT_EXPECT_EQ(test, apple_pmgr_ps_program_min(ps), 0);
	KUNIT_EXPECT_EQ(test, FIELD_GET(APPLE_PMGR_PS_MIN, r->val), APPLE_PMGR_PS_CLKGATE);
}

static void pmgr_ps_auto_state_error_test(struct kunit *test)
{
	struct pmgr_ps_test_reg *r;
	struct apple_pmgr_ps *ps;

	ps = pmgr_ps_test_domain(test, APPLE_PMGR_PS_ACTIVE, false, &r);
	r->fail_read = true;
	KUNIT_EXPECT_EQ(test, apple_pmgr_ps_program_auto(ps), -EIO);
	KUNIT_EXPECT_EQ(test, r->writes, 0U);
	r->fail_read = false;
	r->fail_write = 1;
	KUNIT_EXPECT_EQ(test, apple_pmgr_ps_program_auto(ps), -EIO);
	KUNIT_EXPECT_EQ(test, r->writes, 1U);
	r->fail_write = 0;
	KUNIT_EXPECT_EQ(test, apple_pmgr_ps_program_auto(ps), 0);
	KUNIT_EXPECT_TRUE(test, !!(r->val & APPLE_PMGR_AUTO_ENABLE));
	ps->no_auto_pm = true;
	KUNIT_EXPECT_EQ(test, apple_pmgr_ps_program_auto(ps), 0);
	KUNIT_EXPECT_FALSE(test, !!(r->val & APPLE_PMGR_AUTO_ENABLE));
}

/*
 * A power-off that does not complete is undone: the domain is requested on
 * again, with automatic PM only when the domain allows it.
 */
static void pmgr_ps_power_off_undo_test(struct kunit *test)
{
	struct pmgr_ps_test_reg *r;
	struct apple_pmgr_ps *ps;
	bool no_auto_pm;

	for (no_auto_pm = false; ; no_auto_pm = true) {
		ps = pmgr_ps_test_domain(test, APPLE_PMGR_PS_ACTIVE, no_auto_pm, &r);
		r->stuck = true;
		KUNIT_EXPECT_EQ(test, apple_pmgr_ps_power_off(&ps->genpd), -ETIMEDOUT);
		KUNIT_EXPECT_EQ(test, FIELD_GET(APPLE_PMGR_PS_TARGET, r->val),
				APPLE_PMGR_PS_ACTIVE);
		KUNIT_EXPECT_EQ_MSG(test, !!(r->val & APPLE_PMGR_AUTO_ENABLE), !no_auto_pm,
				    "no_auto_pm %d", no_auto_pm);
		KUNIT_EXPECT_EQ_MSG(test, r->auto_writes, no_auto_pm ? 0U : 1U,
				    "no_auto_pm %d", no_auto_pm);
		if (no_auto_pm)
			break;
	}
}

/* A power-on that does not complete never enabled automatic PM on such a domain. */
static void pmgr_ps_power_on_fails_test(struct kunit *test)
{
	struct pmgr_ps_test_reg *r;
	struct apple_pmgr_ps *ps;

	ps = pmgr_ps_test_domain(test, APPLE_PMGR_PS_PWRGATE, true, &r);
	r->stuck = true;
	KUNIT_EXPECT_EQ(test, apple_pmgr_ps_power_on(&ps->genpd), -ETIMEDOUT);
	/* Undone: requested off again */
	KUNIT_EXPECT_EQ(test, FIELD_GET(APPLE_PMGR_PS_TARGET, r->val), APPLE_PMGR_PS_PWRGATE);
	KUNIT_EXPECT_EQ(test, r->auto_writes, 0U);
}

/* Propagate failed writes and never enable auto after a failed transition. */
static void pmgr_ps_write_failure_test(struct kunit *test)
{
	struct pmgr_ps_test_reg *r;
	struct apple_pmgr_ps *ps;

	ps = pmgr_ps_test_domain(test, APPLE_PMGR_PS_PWRGATE, false, &r);
	r->fail_write = 1;
	KUNIT_EXPECT_EQ(test, apple_pmgr_ps_set(&ps->genpd, APPLE_PMGR_PS_ACTIVE, true), -EIO);
	KUNIT_EXPECT_EQ(test, r->writes, 1U);
	KUNIT_EXPECT_EQ(test, r->auto_writes, 0U);
	kunit_device_unregister(test, ps->dev);

	ps = pmgr_ps_test_domain(test, APPLE_PMGR_PS_PWRGATE, false, &r);
	r->fail_write = 2;
	KUNIT_EXPECT_EQ(test, apple_pmgr_ps_set(&ps->genpd, APPLE_PMGR_PS_ACTIVE, true), -EIO);
	KUNIT_EXPECT_EQ(test, r->writes, 2U);
	KUNIT_EXPECT_EQ(test, r->auto_writes, 0U);
	kunit_device_unregister(test, ps->dev);

	ps = pmgr_ps_test_domain(test, APPLE_PMGR_PS_ACTIVE, false, &r);
	ps->force_disable = true;
	r->fail_write = 1;
	KUNIT_EXPECT_EQ(test, apple_pmgr_ps_set(&ps->genpd, APPLE_PMGR_PS_PWRGATE, false), -EIO);
	KUNIT_EXPECT_EQ(test, r->writes, 1U);
}

static void pmgr_ps_timeout_does_not_enable_auto_test(struct kunit *test)
{
	struct pmgr_ps_test_reg *r;
	struct apple_pmgr_ps *ps;

	ps = pmgr_ps_test_domain(test, APPLE_PMGR_PS_PWRGATE, false, &r);
	r->stuck = true;
	KUNIT_EXPECT_EQ(test, apple_pmgr_ps_set(&ps->genpd, APPLE_PMGR_PS_ACTIVE, true), -ETIMEDOUT);
	KUNIT_EXPECT_EQ(test, r->auto_writes, 0U);
	KUNIT_EXPECT_FALSE(test, !!(r->val & APPLE_PMGR_AUTO_ENABLE));
}

static void pmgr_ps_disable_timeout_stops_test(struct kunit *test)
{
	struct pmgr_ps_test_reg *r;
	struct apple_pmgr_ps *ps;

	ps = pmgr_ps_test_domain(test, APPLE_PMGR_PS_ACTIVE, false, &r);
	ps->force_disable = true;
	r->disable_stuck = true;
	KUNIT_EXPECT_EQ(test, apple_pmgr_ps_set(&ps->genpd, APPLE_PMGR_PS_PWRGATE, false), -ETIMEDOUT);
	KUNIT_EXPECT_EQ(test, r->writes, 1U);
	KUNIT_EXPECT_EQ(test, FIELD_GET(APPLE_PMGR_PS_TARGET, r->val), APPLE_PMGR_PS_ACTIVE);
}

#if IS_ENABLED(CONFIG_APPLE_T6034_GPU_DIAG_NO_AUTO)
static void pmgr_ps_t6034_afr_scope_test(struct kunit *test)
{
	KUNIT_EXPECT_TRUE(test, t6034_gpu_diag_afr_match(true, true, true, true, true,
							true, false, 0x408e80000ULL, 0x4000, 0x100));
	KUNIT_EXPECT_FALSE(test, t6034_gpu_diag_afr_match(false, true, true, true, true,
							 true, false, 0x408e80000ULL, 0x4000, 0x100));
	KUNIT_EXPECT_FALSE(test, t6034_gpu_diag_afr_match(true, false, true, true, true,
							 true, false, 0x408e80000ULL, 0x4000, 0x100));
	KUNIT_EXPECT_FALSE(test, t6034_gpu_diag_afr_match(true, true, false, true, true,
							 true, false, 0x408e80000ULL, 0x4000, 0x100));
	KUNIT_EXPECT_FALSE(test, t6034_gpu_diag_afr_match(true, true, true, false, true,
							 true, false, 0x408e80000ULL, 0x4000, 0x100));
	KUNIT_EXPECT_FALSE(test, t6034_gpu_diag_afr_match(true, true, true, true, false,
							 true, false, 0x408e80000ULL, 0x4000, 0x100));
	KUNIT_EXPECT_FALSE(test, t6034_gpu_diag_afr_match(true, true, true, true, true,
							 false, false, 0x408e80000ULL, 0x4000, 0x100));
	KUNIT_EXPECT_FALSE(test, t6034_gpu_diag_afr_match(true, true, true, true, true,
							 true, true, 0x408e80000ULL, 0x4000, 0x100));
	KUNIT_EXPECT_FALSE(test, t6034_gpu_diag_afr_match(true, true, true, true, true,
							 true, false, 0x408e80000ULL, 0x4000, 0x110));
	KUNIT_EXPECT_FALSE(test, t6034_gpu_diag_afr_match(true, true, true, true, true,
							 true, false, 0x408e90000ULL, 0x4000, 0x100));
}

static void pmgr_ps_t6034_afr_hold_test(struct kunit *test)
{
	struct pmgr_ps_test_reg *r;
	struct apple_pmgr_ps *ps;

	/* A domain already active without auto-PM needs no register write. */
	ps = pmgr_ps_test_domain(test, APPLE_PMGR_PS_ACTIVE, true, &r);
	KUNIT_EXPECT_EQ(test, t6034_gpu_diag_hold_afr(ps), 0);
	KUNIT_EXPECT_EQ(test, r->writes, 0U);
	kunit_device_unregister(test, ps->dev);

	/* Target-on but still inactive without auto-PM is not a stable hold. */
	ps = pmgr_ps_test_domain(test, APPLE_PMGR_PS_PWRGATE, true, &r);
	r->val = FIELD_PREP(APPLE_PMGR_PS_TARGET, APPLE_PMGR_PS_ACTIVE);
	KUNIT_EXPECT_EQ(test, t6034_gpu_diag_hold_afr(ps), -EINVAL);
	KUNIT_EXPECT_EQ(test, r->writes, 0U);
	kunit_device_unregister(test, ps->dev);

	/* Reproduce the observed target-on, gated, automatic state. */
	ps = pmgr_ps_test_domain(test, APPLE_PMGR_PS_PWRGATE, true, &r);
	r->val = APPLE_PMGR_AUTO_ENABLE | FIELD_PREP(APPLE_PMGR_PS_TARGET, APPLE_PMGR_PS_ACTIVE);
	KUNIT_EXPECT_EQ(test, t6034_gpu_diag_hold_afr(ps), 0);
	KUNIT_EXPECT_EQ(test, FIELD_GET(APPLE_PMGR_PS_ACTUAL, r->val), APPLE_PMGR_PS_ACTIVE);
	KUNIT_EXPECT_FALSE(test, !!(r->val & APPLE_PMGR_AUTO_ENABLE));
	kunit_device_unregister(test, ps->dev);

	/* If the hardware never becomes active, restore the observed auto bit. */
	ps = pmgr_ps_test_domain(test, APPLE_PMGR_PS_PWRGATE, true, &r);
	r->val = APPLE_PMGR_AUTO_ENABLE | FIELD_PREP(APPLE_PMGR_PS_TARGET, APPLE_PMGR_PS_ACTIVE);
	r->stuck = true;
	KUNIT_EXPECT_EQ(test, t6034_gpu_diag_hold_afr(ps), -ETIMEDOUT);
	KUNIT_EXPECT_TRUE(test, !!(r->val & APPLE_PMGR_AUTO_ENABLE));
	kunit_device_unregister(test, ps->dev);

	/* An unrecognized initial target is refused before any write. */
	ps = pmgr_ps_test_domain(test, APPLE_PMGR_PS_PWRGATE, true, &r);
	r->val = APPLE_PMGR_AUTO_ENABLE;
	KUNIT_EXPECT_EQ(test, t6034_gpu_diag_hold_afr(ps), -EINVAL);
	KUNIT_EXPECT_EQ(test, r->writes, 0U);
	kunit_device_unregister(test, ps->dev);

	/* Failure to restore automatic gating is reported, not hidden. */
	ps = pmgr_ps_test_domain(test, APPLE_PMGR_PS_PWRGATE, true, &r);
	r->val = APPLE_PMGR_AUTO_ENABLE | FIELD_PREP(APPLE_PMGR_PS_TARGET, APPLE_PMGR_PS_ACTIVE);
	r->stuck = true;
	r->fail_write = 2;
	KUNIT_EXPECT_EQ(test, t6034_gpu_diag_hold_afr(ps), -EIO);
	KUNIT_EXPECT_FALSE(test, !!(r->val & APPLE_PMGR_AUTO_ENABLE));
}

static void pmgr_ps_t6034_diag_scope_test(struct kunit *test)
{
	KUNIT_EXPECT_TRUE(test, t6034_gpu_diag_match(true, true, true, true,
						    0x408e80000ULL, 0x4000, 0x110));
	KUNIT_EXPECT_FALSE(test, t6034_gpu_diag_match(false, true, true, true,
						     0x408e80000ULL, 0x4000, 0x110));
	KUNIT_EXPECT_FALSE(test, t6034_gpu_diag_match(true, false, true, true,
						     0x408e80000ULL, 0x4000, 0x110));
	KUNIT_EXPECT_FALSE(test, t6034_gpu_diag_match(true, true, false, true,
						     0x408e80000ULL, 0x4000, 0x110));
	KUNIT_EXPECT_FALSE(test, t6034_gpu_diag_match(true, true, true, false,
						     0x408e80000ULL, 0x4000, 0x110));
	KUNIT_EXPECT_FALSE(test, t6034_gpu_diag_match(true, true, true, true,
						     0x408e80000ULL, 0x4000, 0x100));
	KUNIT_EXPECT_FALSE(test, t6034_gpu_diag_match(true, true, true, true,
						     0x408e80000ULL, 0x1000, 0x110));
	KUNIT_EXPECT_FALSE(test, t6034_gpu_diag_match(true, true, true, true,
						     0x408e90000ULL, 0x4000, 0x110));
}

/* Optional fake J514M QEMU tree: exercise actual OF parsing, never MMIO. */
static void pmgr_ps_t6034_diag_of_test(struct kunit *test)
{
	struct device_node *node;
	bool saved = t6034_gpu_diag_no_auto;
	bool saved_afr = t6034_gpu_diag_afr_hold;

	if (!of_machine_is_compatible("linux,dummy-virt") ||
	    !of_machine_is_compatible("apple,j514m")) {
		kunit_skip(test, "requires the synthetic J514M QEMU device tree");
		return;
	}
	node = of_find_node_by_path("/soc/power-management@408e80000/power-controller@110");
	KUNIT_ASSERT_NOT_NULL(test, node);
	t6034_gpu_diag_no_auto = false;
	KUNIT_EXPECT_FALSE(test, t6034_gpu_diag_domain(node, 0x110));
	t6034_gpu_diag_no_auto = true;
	KUNIT_EXPECT_TRUE(test, t6034_gpu_diag_domain(node, 0x110));
	KUNIT_EXPECT_FALSE(test, t6034_gpu_diag_domain(node, 0x100));
	of_node_put(node);

	node = of_find_node_by_path("/soc/power-management@408e80000/power-controller@100");
	KUNIT_ASSERT_NOT_NULL(test, node);
	t6034_gpu_diag_afr_hold = false;
	KUNIT_EXPECT_FALSE(test, t6034_gpu_diag_afr_domain(node, 0x100));
	t6034_gpu_diag_afr_hold = true;
	KUNIT_EXPECT_EQ(test, t6034_gpu_diag_afr_domain(node, 0x100),
			 of_property_read_bool(node, "apple,always-on"));
	KUNIT_EXPECT_FALSE(test, t6034_gpu_diag_afr_domain(node, 0x110));
	t6034_gpu_diag_no_auto = false;
	KUNIT_EXPECT_FALSE(test, t6034_gpu_diag_afr_domain(node, 0x100));
	t6034_gpu_diag_no_auto = saved;
	t6034_gpu_diag_afr_hold = saved_afr;
	of_node_put(node);
}
#endif

static struct kunit_case pmgr_ps_test_cases[] = {
	KUNIT_CASE(pmgr_ps_power_on_test),
	KUNIT_CASE(pmgr_ps_initial_state_test),
	KUNIT_CASE(pmgr_ps_min_state_error_test),
	KUNIT_CASE(pmgr_ps_auto_state_error_test),
	KUNIT_CASE(pmgr_ps_power_off_undo_test),
	KUNIT_CASE(pmgr_ps_power_on_fails_test),
	KUNIT_CASE(pmgr_ps_write_failure_test),
	KUNIT_CASE(pmgr_ps_timeout_does_not_enable_auto_test),
	KUNIT_CASE(pmgr_ps_disable_timeout_stops_test),
#if IS_ENABLED(CONFIG_APPLE_T6034_GPU_DIAG_NO_AUTO)
	KUNIT_CASE(pmgr_ps_t6034_afr_scope_test),
	KUNIT_CASE(pmgr_ps_t6034_afr_hold_test),
	KUNIT_CASE(pmgr_ps_t6034_diag_scope_test),
	KUNIT_CASE(pmgr_ps_t6034_diag_of_test),
#endif
	{}
};

static struct kunit_suite pmgr_ps_test_suite = {
	.name = "apple-pmgr-pwrstate",
	.test_cases = pmgr_ps_test_cases,
};

kunit_test_suite(pmgr_ps_test_suite);
